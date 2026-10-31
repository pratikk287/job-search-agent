"""One-click "Auto-fill & apply" for a job.

A single click does everything for that job, and shows progress step by step:
  1. (optional) customise the resume and cover letter
  2. open the application form in Chrome and fill it in (the candidate presses Submit)
  3. find the HR and hiring-team people, save their LinkedIn notes to the Sheet
  4. email HR / the hiring team, only to addresses that pass the cross-check

The click itself is the approval for these emails, so nothing else needs approving.
"""
import threading

from . import config, contacts, db, documents, outreach, people

sessions = {}        # job_id -> {"steps": [...], "finished": bool}
_lock = threading.Lock()


def _set(job_id, key, state, detail=""):
    with _lock:
        for s in sessions[job_id]["steps"]:
            if s["key"] == key:
                s["state"], s["detail"] = state, detail


def status(job_id):
    return sessions.get(job_id)


def start(job_id, tailor=False, resume_key=None, email=True, review=True):
    steps = []
    if tailor:
        steps.append({"key": "docs", "label": "Customising your resume and cover letter", "state": "pending", "detail": ""})
    steps += [
        {"key": "form", "label": "Opening the application form", "state": "pending", "detail": ""},
        {"key": "people", "label": "Finding HR and the hiring team", "state": "pending", "detail": ""},
        {"key": "emails", "label": "Emailing HR and the hiring team", "state": "pending" if email else "skipped",
         "detail": "" if email else "Turned off"},
        {"key": "sheet", "label": "Saving LinkedIn notes to the Sheet", "state": "pending", "detail": ""},
    ]
    with _lock:
        if sessions.get(job_id) and not sessions[job_id]["finished"]:
            return sessions[job_id]
        sessions[job_id] = {"steps": steps, "finished": False}
    threading.Thread(target=_run, args=(job_id, tailor, resume_key, email, review), daemon=True).start()
    return sessions[job_id]


def _open_form(job_id):
    a = outreach.create_application(job_id)
    if a["status"] != "approved":
        db.update("actions", "id", a["id"], {"status": "approved", "approved_at": db.now(), "result": None})
    outreach.execute(a["id"])
    return outreach.get(a["id"])


ROLES = "('recruiter', 'hiring_manager', 'department_head')"


def email_review(job_id):
    """The people for this job with the address we would use, so the candidate can fix or add one before sending."""
    out = []
    rows = db.query(f"SELECT * FROM contacts WHERE job_id = ? AND role_type IN {ROLES} "
                    "ORDER BY CASE role_type WHEN 'recruiter' THEN 0 ELSE 1 END, email_confidence DESC", (job_id,))
    for c in rows:
        sent = bool(db.one("SELECT 1 FROM actions WHERE contact_id = ? AND kind = 'email' AND status = 'done'", (c["id"],)))
        ok, why = contacts.email_trust(c["email"], c) if c.get("email") else (False, "No address yet. Type one in.")
        out.append({"contact_id": c["id"], "name": c["name"], "title": c["title"], "who": "hr" if c["role_type"] == "recruiter" else "team",
                    "email": c.get("email") or "", "status": c.get("email_status"), "ok": ok, "reason": why, "sent": sent})
    return out


def _apply_edits(job_id, items):
    """Save addresses the candidate typed. A typed address counts as known, so it is trusted and teaches the company's format."""
    only = []
    for it in items:
        c = db.one("SELECT * FROM contacts WHERE id = ? AND job_id = ?", (it.get("contact_id"), job_id))
        if not c:
            continue
        addr = (it.get("email") or "").strip().lower()
        if it.get("send") and addr:
            if "@" not in addr or "." not in addr.split("@")[-1] or " " in addr:
                raise ValueError(f"'{addr}' is not a valid email address")
            if addr != (c.get("email") or "").lower():
                db.execute("DELETE FROM actions WHERE contact_id = ? AND kind = 'email' AND status IN ('draft', 'approved', 'failed')", (c["id"],))
                db.update("contacts", "id", c["id"], {"email": addr, "email_status": "public", "email_confidence": 1.0})
                db.execute("UPDATE li_outreach SET email_guess = ? WHERE contact_id = ?", (addr, c["id"]))
                try:
                    contacts.learn_pattern(addr, c.get("name"))
                except Exception:
                    pass
            only.append(c["id"])
    return only


def send_emails(job_id, items=None):
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    only = None
    if items is not None:
        only = _apply_edits(job_id, items)
        if not only:
            return "skipped", "Nothing was ticked to send."
    state, detail = _send_emails(job, job_id, only)
    if sessions.get(job_id):
        _set(job_id, "emails", state, detail)
    return state, detail


def _send_emails(job, job_id, only=None):
    """Email up to the allowed number of people (HR first, then the hiring team) whose address passes the cross-check."""
    if not config.integrations()["smtp"]:
        return "skipped", "Gmail is not connected (Settings > Gmail)"
    rows = db.query(f"SELECT * FROM contacts WHERE job_id = ? AND email IS NOT NULL AND email != '' "
                    f"AND role_type IN {ROLES} "
                    "ORDER BY CASE role_type WHEN 'recruiter' THEN 0 ELSE 1 END, email_confidence DESC", (job_id,))
    if only is not None:
        rows = [c for c in rows if c["id"] in only]
    sent, skipped = [], []
    for c in rows:
        if only is None and len(sent) >= people.people_per_job():
            break
        if db.one("SELECT 1 FROM actions WHERE contact_id = ? AND kind = 'email' AND status = 'done'", (c["id"],)):
            continue                                         # already emailed
        ok, why = contacts.email_trust(c["email"], c)
        if not ok:
            skipped.append(f"{c['name'] or c['email']}: {why}")
            continue
        draft = db.one("SELECT id FROM actions WHERE contact_id = ? AND kind = 'email' AND status = 'draft'", (c["id"],))
        if draft:
            aid = draft["id"]
        else:
            subject, body = outreach.draft_email(job, c)
            body, _ = outreach._polish(body, job, "email")
            aid = outreach._create("email", job_id, c["id"], "smtp", subject, body, c["email"],
                                   attachments=outreach._resume_attachment(job_id))
        try:
            outreach.approve(aid)
            res = outreach.execute(aid)
            (sent if res["status"] == "done" else skipped).append(
                c["name"] or c["email"] if res["status"] == "done" else f"{c['name'] or c['email']}: {(res.get('result') or {}).get('error', 'not sent')}")
        except Exception as e:
            skipped.append(f"{c['name'] or c['email']}: {e}")
    if not sent and not rows:
        return "skipped", "No verified email address found. LinkedIn notes are in the Sheet instead."
    detail = ("Sent to " + ", ".join(sent) + ". Addresses guessed from the company's format are marked unverified; bounces are tracked.") if sent else "Nothing sent"
    if skipped:
        detail += ". Skipped: " + "; ".join(skipped)
    return ("done" if sent else "skipped"), detail


def _run(job_id, tailor, resume_key, email, review=True):
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    try:
        if tailor:
            _set(job_id, "docs", "running")
            r = documents.prepare(job_id, resume_key=resume_key)
            _set(job_id, "docs", "done", f"Resume {r['resume_key']} customised" + (f". {r['warnings'][0]}" if r["warnings"] else ""))
        else:
            db.execute("DELETE FROM documents WHERE job_id = ?", (job_id,))      # use your original resume as it is
            if resume_key:
                db.update("jobs", "id", job_id, {"resume_key": resume_key})
        job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        _set(job_id, "form", "running")
        a = _open_form(job_id)
        err = (a.get("result") or {}).get("error") if isinstance(a.get("result"), dict) else None
        _set(job_id, "form", "failed" if a["status"] == "failed" else "done",
             err or "Chrome is opening the form. Check it, then press Submit there.")
        _set(job_id, "people", "running")
        if not config.integrations()["naukri"]:
            _set(job_id, "people", "skipped", "Needs the Firecrawl key (Settings)")
            _set(job_id, "sheet", "skipped", "")
            _set(job_id, "emails", "skipped", "")
        else:
            r = people.research_job(job_id)
            _set(job_id, "people", "done" if r["people"] else "skipped",
                 f"{len(r['people'])} found" if r["people"] else (r["notes"][0] if r["notes"] else "No one found"))
            _set(job_id, "sheet", "done" if r["people"] else "skipped", f"{len(r['people'])} added" if r["people"] else "")
            if email and review and r["people"]:
                _set(job_id, "emails", "waiting", "Check the addresses below, fix any that look wrong, then press Send.")
            elif email:
                _set(job_id, "emails", "running")
                st, detail = _send_emails(job, job_id)
                _set(job_id, "emails", st, detail)
    except Exception as e:
        for s in sessions[job_id]["steps"]:
            if s["state"] in ("running", "pending"):
                s["state"], s["detail"] = "failed", f"{type(e).__name__}: {str(e)[:100]}"
    finally:
        sessions[job_id]["finished"] = True
        db.log_event(job_id, "auto_apply", {"steps": [(s["key"], s["state"]) for s in sessions[job_id]["steps"]]})
