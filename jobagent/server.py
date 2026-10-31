"""HTTP API + dashboard. Run with:  python run.py"""
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import apply as autoapply
from . import mix
from . import config, contacts, db, documents, outreach, people, report, resumes, scan, verify
from .keywords import FUNCTIONS
from .sources import aggregators, importer
from .sources.common import age_days
from .sources.employers import detect_ats

app = FastAPI(title="Job Search Agent")
STATUSES = ["new", "shortlisted", "docs_ready", "applied", "interviewing", "offer", "rejected", "archived"]


def _job_out(j, full=False):
    j = dict(j)
    j["age_days"] = round(age_days(j.get("posted_at")), 1) if j.get("posted_at") else None
    j["score_breakdown"] = json.loads(j["score_breakdown"]) if j.get("score_breakdown") else None
    j["verification_notes"] = json.loads(j["verification_notes"]) if j.get("verification_notes") else None
    j["function_label"] = FUNCTIONS.get(j.get("function") or "", {}).get("label")
    j.pop("raw", None)
    if not full:
        j.pop("description", None)
    return j


def _err(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ meta

@app.get("/api/health")
def health():
    return {"integrations": config.integrations(), "settings": config.settings(),
            "issues": resumes.consistency_issues(), "scan": {k: scan.state[k] for k in ("running", "started_at")}}


@app.get("/api/credits")
def credits():
    s = config.settings()
    snap = mix.snapshot(refresh=True, max_age=0)
    integ = config.integrations()
    share = mix.effective_share(s, snap)
    return {"credits": snap, "company_share_setting": s.get("company_share", 0.5), "company_share_now": round(share, 2),
            "auto_balance": s.get("auto_balance", True), "plan": mix.allocate(len(config.companies()), share, s, snap, integ),
            "company_sites": len(config.companies())}


@app.get("/api/profile")
def get_profile():
    p = config.profile()
    return {"profile": p, "resumes": {k: {kk: v[kk] for kk in ("label", "file", "functions", "skills_detected", "profile")}
                                      for k, v in resumes.all_resumes().items()},
            "issues": resumes.consistency_issues()}


@app.put("/api/profile")
def put_profile(fields: dict = Body(...)):
    p = config.profile()
    editable = {"current_title", "notice_period", "linkedin_url", "portfolio_url", "phone", "email",
                "total_experience_years", "post_mba_experience_years", "current_city", "expected_ctc_lpa",
                "current_ctc", "open_locations", "reason_for_leaving", "work_mode", "willing_to_relocate",
                "previously_applied", "referral", "school_scores", "relevant_experience"}
    for k, v in fields.items():
        if k in editable:
            p[k] = v
    for e in p.get("education", []):          # grades come in as {"mba": "...", "bsc": "..."}
        key = e["degree"].split()[0].lower().strip(",")
        if key in fields.get("degree_scores", {}):
            e["score"] = fields["degree_scores"][key]
    # Saving the experience figures counts as confirming them, even if they differ from the resume dates.
    confirmed = set(p.get("confirmed_fields", []))
    confirmed |= {k for k in ("total_experience_years", "post_mba_experience_years") if k in fields}
    p["confirmed_fields"] = sorted(confirmed)
    config.save_profile(p)
    resumes.load.cache_clear()
    return get_profile()


@app.put("/api/settings")
def put_settings(fields: dict = Body(...)):
    s = config.settings()
    if "max_job_age_days" in fields:
        fields["max_job_age_days"] = max(1, min(10, int(fields["max_job_age_days"])))   # hard ceiling: 10 days
    for k in ("max_job_age_days", "default_view_age_days", "min_score_to_shortlist", "followup_after_days",
              "max_emails_per_day", "search_keywords", "scan_cities", "require_posted_date", "people_per_job", "company_share", "auto_balance", "group_weights",
              "apify_results_per_query", "apify_max_usd_per_scan"):
        if k in fields:
            s[k] = fields[k]
    config.save_settings(s)
    return s


@app.get("/api/companies")
def get_companies():
    return config.companies()


@app.post("/api/companies")
def add_company(body: dict = Body(...)):
    url, name, domain = body.get("careers_url", ""), body.get("name", "").strip(), body.get("domain", "").strip()
    if not (url and name):
        raise HTTPException(400, "name and careers_url are required")
    try:
        ats = detect_ats(url)
    except Exception as e:
        raise HTTPException(400, f"Could not open the careers page: {e}")
    if not ats:
        raise HTTPException(400, "No supported job board (Greenhouse, Lever, Ashby, SmartRecruiters, Workday) found on that page. "
                                 "Use 'Import a job' for individual postings, or rely on the portal aggregators.")
    items = config.companies()
    items.append({"name": name, **ats, "domain": domain or None})
    config.save_companies(items)
    return {"added": items[-1]}


@app.delete("/api/companies/{name}")
def delete_company(name: str):
    config.save_companies([c for c in config.companies() if c["name"] != name])
    return {"ok": True}


# ------------------------------------------------------------------ discovery

@app.post("/api/scan")
def start_scan(body: dict = Body(default={})):
    started = scan.start_background(include_employers=body.get("employers", True),
                                    include_aggregators=body.get("aggregators", True))
    return {"started": started, **{k: scan.state[k] for k in ("running", "started_at")}}


@app.post("/api/scan/stop")
def stop_scan():
    if scan.state["running"]:
        scan.state["stop"] = True
        scan.state["label"] = "Stopping..."
    return {"stopping": scan.state["running"]}


@app.get("/api/scan")
def scan_status():
    last = db.one("SELECT * FROM scans WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1")
    return {**scan.state, "last": {**last, "summary": json.loads(last["summary"])} if last else None}


@app.get("/api/portals")
def portals(keyword: str = "product marketing manager", city: str = "Bengaluru", days: int = 7):
    return aggregators.portal_links(keyword, city, min(days, config.settings()["max_job_age_days"]))


@app.post("/api/jobs/import")
def import_job(body: dict = Body(...)):
    s = config.settings()
    problems = []
    if body.get("url") and not body.get("title"):
        try:
            job, problems = importer.from_url(body["url"])
        except Exception as e:
            raise HTTPException(400, f"Could not read that page ({e}). Paste the job details instead.")
        if not job:
            raise HTTPException(400, " ".join(problems))
        if body.get("preview"):
            return {"job": {k: v for k, v in job.items() if k != "raw"}, "problems": problems}
    else:
        job = importer.from_text(body.get("title", ""), body.get("company", ""), body.get("location", ""),
                                 body.get("description", ""), body.get("url", ""), body.get("posted_at"))
    for k in ("title", "company", "location", "posted_at"):
        if body.get(k):
            job[k] = body[k] if k != "posted_at" else importer.parse_date(body[k])
    if not job.get("title") or not job.get("company"):
        raise HTTPException(400, "Title and company are required")
    age = age_days(job.get("posted_at"))
    if age is None:
        raise HTTPException(400, "Posting date unknown. Enter the date the job was posted (jobs older than "
                                 f"{s['max_job_age_days']} days are not tracked).")
    if age > s["max_job_age_days"]:
        raise HTTPException(400, f"This job was posted {age:.0f} days ago, beyond the {s['max_job_age_days']}-day limit.")
    job_id, is_new = scan.store(job, verify_now=True)
    return {"id": job_id, "new": is_new, "problems": problems, "job": _job_out(db.one("SELECT * FROM jobs WHERE id = ?", (job_id,)))}


@app.get("/api/jobs")
def list_jobs(max_age: Optional[float] = None, min_score: float = 0, status: str = "", function: str = "",
              q: str = "", include_closed: bool = False):
    s = config.settings()
    max_age = min(max_age or s["max_job_age_days"], s["max_job_age_days"])
    rows = sorted(db.query("SELECT * FROM jobs WHERE status != 'duplicate'"), key=lambda j: -scan.priority(j))
    out = []
    for j in rows:
        a = age_days(j["posted_at"])
        tracked = j["status"] in ("applied", "interviewing", "offer", "rejected")
        if not tracked and (a is None or a > max_age):
            continue
        if (j["score"] or 0) < min_score:
            continue
        if status and j["status"] != status:
            continue
        if not status and j["status"] == "archived":
            continue
        if function and j["function"] != function:
            continue
        if not include_closed and j["verification_status"] in ("closed", "suspicious") and not tracked:
            continue
        if q and q.lower() not in f"{j['title']} {j['company']} {j['city']}".lower():
            continue
        out.append(_job_out(j))
    app_state = {r["job_id"]: r["status"] for r in db.query("SELECT job_id, status FROM actions WHERE kind = 'application' ORDER BY id")}
    people_n = {r["job_id"]: r["n"] for r in db.query("SELECT job_id, COUNT(*) n FROM li_outreach GROUP BY job_id")}
    for j in out:
        j["app_status"] = app_state.get(j["id"])
        j["people"] = people_n.get(j["id"], 0)
    return out


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not j:
        raise HTTPException(404, "Job not found")
    out = _job_out(j, full=True)
    out["documents"] = [{**d, "name": Path(d["path"]).name} for d in db.query("SELECT * FROM documents WHERE job_id = ?", (job_id,))]
    out["contacts"] = contacts.contacts_for(job_id)
    out["actions"] = [outreach.get(a["id"]) for a in db.query("SELECT id FROM actions WHERE job_id = ? ORDER BY id", (job_id,))]
    out["events"] = db.query("SELECT * FROM events WHERE job_id = ? ORDER BY id DESC LIMIT 50", (job_id,))
    out["linkedin_search"] = contacts.linkedin_search_links(j)
    return out


@app.post("/api/jobs/{job_id}/status")
def set_status(job_id: str, body: dict = Body(...)):
    st = body.get("status")
    if st not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    fields = {"status": st}
    if st == "applied":
        fields["applied_at"] = db.now()
    if "notes" in body:
        fields["notes"] = body["notes"]
    db.update("jobs", "id", job_id, fields)
    db.log_event(job_id, "status_changed", st)
    return _job_out(db.one("SELECT * FROM jobs WHERE id = ?", (job_id,)))


@app.post("/api/jobs/{job_id}/verify")
def verify_job(job_id: str):
    return verify.verify_job(job_id, fetch_page=True)


@app.post("/api/jobs/{job_id}/prepare")
def prepare(job_id: str, body: dict = Body(default={})):
    return documents.prepare(job_id, resume_key=body.get("resume_key"), contact_name=body.get("contact_name"))


@app.get("/api/documents/{doc_id}")
def download(doc_id: int):
    d = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not d or not Path(d["path"]).exists():
        raise HTTPException(404)
    return FileResponse(d["path"], filename=Path(d["path"]).name)


@app.get("/api/documents/{doc_id}/text")
def document_text(doc_id: int):
    d = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not d or not d["path"].endswith(".txt"):
        raise HTTPException(404)
    return {"text": Path(d["path"]).read_text(encoding="utf-8")}


@app.put("/api/documents/{doc_id}/text")
def save_document_text(doc_id: int, body: dict = Body(...)):
    d = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not d or not d["path"].endswith(".txt"):
        raise HTTPException(404)
    Path(d["path"]).write_text(body["text"], encoding="utf-8")
    docx = db.one("SELECT * FROM documents WHERE job_id = ? AND kind = 'cover_letter_docx'", (d["job_id"],))
    if docx:
        documents._docx_letter(body["text"], docx["path"])
    return {"ok": True}


# ------------------------------------------------------------------ contacts & outreach

@app.post("/api/jobs/{job_id}/contacts/discover")
def discover(job_id: str):
    return contacts.discover(job_id)


@app.post("/api/jobs/{job_id}/contacts")
def add_contact(job_id: str, body: dict = Body(...)):
    if not body.get("name") and not body.get("email"):
        raise HTTPException(400, "Name or email required")
    return contacts.add_manual(job_id, body.get("name"), body.get("title", ""), body.get("email") or None,
                               body.get("linkedin_url") or None, body.get("pattern"))


@app.delete("/api/contacts/{contact_id}")
def delete_contact(contact_id: int):
    db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    return {"ok": True}


@app.post("/api/contacts/{contact_id}/drafts")
def drafts(contact_id: int):
    c = db.one("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    if not c:
        raise HTTPException(404)
    return outreach.create_drafts(c["job_id"], contact_id)


@app.post("/api/jobs/{job_id}/application")
def application(job_id: str):
    return outreach.create_application(job_id)


@app.post("/api/jobs/{job_id}/apply")
def auto_apply(job_id: str, body: dict = Body(default={})):
    if not db.one("SELECT id FROM jobs WHERE id = ?", (job_id,)):
        raise HTTPException(404, "Job not found")
    return autoapply.start(job_id, tailor=bool(body.get("tailor")), resume_key=body.get("resume_key"), email=body.get("email", True), review=body.get("review", True))


@app.get("/api/jobs/{job_id}/apply")
def auto_apply_status(job_id: str):
    return autoapply.status(job_id) or {"steps": [], "finished": True}


@app.get("/api/jobs/{job_id}/email-review")
def email_review(job_id: str):
    return autoapply.email_review(job_id)


@app.post("/api/jobs/{job_id}/email-team")
def email_team(job_id: str, body: dict = Body(default={})):
    try:
        state, detail = autoapply.send_emails(job_id, body.get("emails"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"state": state, "detail": detail}


@app.post("/api/jobs/delete")
def delete_jobs(body: dict = Body(...)):
    """Remove jobs from the list (kept hidden so a rescan does not bring them back). Unsent drafts and untouched Sheet rows go too."""
    n = 0
    for jid in body.get("ids") or []:
        if not db.one("SELECT 1 FROM jobs WHERE id = ?", (jid,)):
            continue
        db.update("jobs", "id", jid, {"status": "archived", "notes": "Removed by you"})
        db.execute("DELETE FROM actions WHERE job_id = ? AND status IN ('draft', 'approved', 'failed')", (jid,))
        db.execute("DELETE FROM li_outreach WHERE job_id = ? AND note_sent_at IS NULL AND message_sent_at IS NULL AND replied_at IS NULL", (jid,))
        db.log_event(jid, "deleted", "removed by user")
        n += 1
    return {"deleted": n}


@app.on_event("startup")
def start_inbox_watcher():
    """Every 5 minutes, look for bounces and replies so tracking never needs a click."""
    import threading
    import time

    def loop():
        while True:
            time.sleep(300)
            try:
                if config.integrations()["imap"]:
                    outreach.check_replies()
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()


@app.post("/api/jobs/{job_id}/submitted")
def mark_submitted(job_id: str):
    a = db.one("SELECT id FROM actions WHERE job_id = ? AND kind = 'application' ORDER BY id DESC LIMIT 1", (job_id,))
    if a:
        db.update("actions", "id", a["id"], {"status": "done", "executed_at": db.now()})
    db.update("jobs", "id", job_id, {"status": "applied", "applied_at": db.now()})
    db.log_event(job_id, "applied", "marked submitted")
    return {"ok": True}


@app.delete("/api/actions/{action_id}")
def delete_action(action_id: int):
    db.execute("DELETE FROM actions WHERE id = ? AND status != 'done'", (action_id,))
    return {"ok": True}


@app.delete("/api/actions")
def delete_all_approvals():
    """Remove every draft, approval and queued item. Emails already sent stay, because bounce and reply tracking need them."""
    n = db.one("SELECT COUNT(*) n FROM actions WHERE status != 'done'")["n"]
    db.execute("DELETE FROM actions WHERE status != 'done'")
    db.execute("UPDATE jobs SET status = 'shortlisted' WHERE status = 'docs_ready'")
    return {"deleted": n}


@app.get("/api/actions")
def list_actions(status: str = ""):
    sql = ("SELECT a.id FROM actions a WHERE (? = '' OR a.status = ?) ORDER BY "
           "CASE a.status WHEN 'approved' THEN 0 WHEN 'executing' THEN 1 WHEN 'draft' THEN 2 WHEN 'failed' THEN 3 ELSE 4 END, a.id DESC")
    out = []
    for r in db.query(sql, (status, status)):
        a = outreach.get(r["id"])
        j = db.one("SELECT company, title FROM jobs WHERE id = ?", (a["job_id"],))
        c = db.one("SELECT name, title, role_type, email_status FROM contacts WHERE id = ?", (a["contact_id"],)) if a["contact_id"] else None
        out.append({**a, "job": j, "contact": c})
    return out


@app.put("/api/actions/{action_id}")
def edit_action(action_id: int, body: dict = Body(...)):
    return _err(outreach.edit, action_id, body)


@app.post("/api/actions/{action_id}/approve")
def approve_action(action_id: int, body: dict = Body(default={})):
    if body.get("confirm") is not True:
        raise HTTPException(400, "Approval needs explicit confirmation")
    a = _err(outreach.approve, action_id)
    if a["kind"] == "application":      # approving a job: find its HR + hiring-team people and fill the outreach sheet
        people.research_in_background(a["job_id"])
    return a


@app.post("/api/actions/{action_id}/reject")
def reject_action(action_id: int):
    return outreach.reject(action_id)


@app.post("/api/actions/{action_id}/execute")
def execute_action(action_id: int):
    return _err(outreach.execute, action_id)


@app.post("/api/actions/{action_id}/done")
def done_action(action_id: int):
    return _err(outreach.mark_done, action_id)


@app.post("/api/actions/{action_id}/replied")
def replied_action(action_id: int):
    return outreach.mark_replied(action_id)


_gmail_check = {}


@app.post("/api/browser/signin")
def browser_signin(body: dict = Body(...)):
    from . import signin
    try:
        return {"opened": signin.open_signin(body.get("site", "linkedin"))}
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/gmail")
def gmail_status():
    """'connected' only when Gmail actually accepts the saved login (checked once per saved password)."""
    user, pwd = config.secret("SMTP_USER"), config.secret("SMTP_PASSWORD")
    if not (user and pwd):
        return {"connected": False, "email": user or None}
    key = (user, hash(pwd))
    if key not in _gmail_check:
        _gmail_check[key] = outreach.test_gmail_login(user, pwd)
    ok, msg = _gmail_check[key]
    return {"connected": ok, "email": user, "problem": None if ok else f"A saved password exists but Gmail rejects it. {msg}"}


@app.post("/api/gmail/connect")
def gmail_connect(body: dict = Body(...)):
    email = (body.get("email") or "").strip().lower()
    pwd = re.sub(r"\s+", "", body.get("app_password") or "")     # Google shows it in groups of four
    if not re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", email):
        raise HTTPException(400, "Enter your Gmail address")
    if len(pwd) != 16:
        raise HTTPException(400, "An app password is 16 letters (Google shows it as four groups of four).")
    ok, msg = outreach.test_gmail_login(email, pwd)
    if not ok:
        raise HTTPException(400, msg)
    config.set_env({"SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587", "IMAP_HOST": "imap.gmail.com",
                    "SMTP_USER": email, "SMTP_PASSWORD": pwd})
    return {"connected": True, "email": email, "message": msg}


@app.post("/api/gmail/disconnect")
def gmail_disconnect():
    config.set_env({"SMTP_PASSWORD": ""})
    return {"connected": False}


@app.post("/api/gmail/test")
def gmail_test():
    to = config.secret("SMTP_USER")
    if not config.integrations()["smtp"]:
        raise HTTPException(400, "Connect Gmail first")
    try:
        outreach.send_email({"recipient": to, "subject": "Job Search Agent - test email", "attachments": [],
                             "body": "This is a test from your Job Search Agent. Approved emails will send from this Gmail.\n\nNo action needed."})
    except Exception as e:
        raise HTTPException(400, f"Send failed: {e}")
    return {"sent_to": to}


@app.post("/api/jobs/{job_id}/outreach/research")
def outreach_research(job_id: str):
    return people.research_job(job_id)


@app.get("/api/email-patterns")
def email_patterns():
    return db.query("SELECT * FROM email_patterns ORDER BY created_at DESC")


@app.post("/api/known-email")
def known_email(body: dict = Body(...)):
    """The candidate found a real address at a company (e.g. on a public profile). Learn the company's format,
    add the person, and queue an email draft for approval."""
    company = (body.get("company") or "").strip()
    address = (body.get("email") or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", address):
        raise HTTPException(400, "Enter a valid email address")
    job = db.one("SELECT * FROM jobs WHERE lower(company) LIKE ? AND status NOT IN ('duplicate','archived') ORDER BY score DESC LIMIT 1",
                 (f"%{company.lower()}%",)) if company else None
    if not job:
        raise HTTPException(400, f"No job from '{company}' is in the app yet. Scan or import one first, so the email has a job to refer to.")
    name = (body.get("name") or "").strip() or contacts.name_from_address(address)
    learned = contacts.learn_pattern(address, name)
    c = contacts.add_manual(job["id"], name or address.split("@")[0], body.get("title") or "", address, body.get("linkedin_url") or None)
    ok, why = contacts.email_trust(address, c)
    draft = None
    if ok:
        subject, text = outreach.draft_email(job, c)
        draft = outreach.get(outreach._create("email", job["id"], c["id"], "smtp", subject, text, address,
                                              attachments=outreach._resume_attachment(job["id"])))
    # refresh guessed addresses for this company's other people using the learned format
    updated = []
    for r in db.query("SELECT * FROM li_outreach WHERE lower(company) LIKE ? AND (email_guess IS NULL OR email_guess != ?)", (f"%{job['company'].lower()}%", address)):
        guess, status = contacts.guess_email(r["name"], learned["domain"])
        if guess:
            db.update("li_outreach", "id", r["id"], {"email_guess": guess})
            if r.get("contact_id"):
                db.update("contacts", "id", r["contact_id"], {"email": guess, "email_status": status})
            updated.append(guess)
    return {"job": job["title"], "company": job["company"], "pattern": learned["pattern"], "domain": learned["domain"],
            "trust": {"ok": ok, "reason": why}, "draft_id": draft["id"] if draft else None, "refreshed_guesses": updated}


@app.get("/api/linkedin")
def linkedin_list():
    rows = db.query("SELECT * FROM li_outreach ORDER BY (note_sent_at IS NOT NULL), id DESC")
    for r in rows:      # only offer an email when the address passes the cross-check
        r["email_ok"], r["email_status"], r["email_sent"] = False, None, False
        c = db.one("SELECT * FROM contacts WHERE id = ?", (r["contact_id"],)) if r.get("contact_id") else None
        if c:
            r["email_status"] = c.get("email_status")
            r["email_sent"] = bool(db.one("SELECT 1 FROM actions WHERE contact_id = ? AND kind = 'email' AND status = 'done'", (c["id"],)))
        if r.get("email_guess"):
            r["email_ok"], r["email_reason"] = contacts.email_trust(r["email_guess"], c)
    return rows


@app.put("/api/linkedin/{row_id}")
def linkedin_update(row_id: int, body: dict = Body(...)):
    fields = {}
    for k in ("note_sent_at", "message_sent_at", "replied_at"):
        if k in body:
            fields[k] = db.now() if body[k] else None
    for k in ("note", "message"):
        if k in body:
            if k == "note" and len(body[k]) > 200:
                raise HTTPException(400, "A LinkedIn connection note can be at most 200 characters")
            fields[k] = body[k]
    if "email" in body:
        addr = (body["email"] or "").strip().lower()
        if addr and ("@" not in addr or "." not in addr.split("@")[-1] or " " in addr):
            raise HTTPException(400, f"'{addr}' is not a valid email address")
        row = db.one("SELECT * FROM li_outreach WHERE id = ?", (row_id,))
        fields["email_guess"] = addr or None
        if row and row.get("contact_id"):
            db.execute("DELETE FROM actions WHERE contact_id = ? AND kind = 'email' AND status IN ('draft', 'approved', 'failed')", (row["contact_id"],))
            db.update("contacts", "id", row["contact_id"], {"email": addr or None, "email_status": "public" if addr else "unknown", "email_confidence": 1.0 if addr else 0})
            if addr:
                try:
                    contacts.learn_pattern(addr, row.get("name"))
                except Exception:
                    pass
    db.update("li_outreach", "id", row_id, fields)
    return db.one("SELECT * FROM li_outreach WHERE id = ?", (row_id,))


@app.post("/api/linkedin/{row_id}/email-draft")
def linkedin_email_draft(row_id: int):
    r = db.one("SELECT * FROM li_outreach WHERE id = ?", (row_id,))
    c = db.one("SELECT * FROM contacts WHERE id = ?", (r["contact_id"],)) if r and r.get("contact_id") else None
    if not c or not c.get("email"):
        raise HTTPException(400, "No email address for this person")
    if c.get("email_status") == "bounced":
        raise HTTPException(400, "This address already bounced")
    if db.one("SELECT 1 FROM actions WHERE contact_id = ? AND kind = 'email' AND status IN ('draft','approved','done')", (c["id"],)):
        raise HTTPException(400, "An email to this person is already in Approvals")
    job = db.one("SELECT * FROM jobs WHERE id = ?", (r["job_id"],))
    subject, body = outreach.draft_email(job, c)
    aid = outreach._create("email", job["id"], c["id"], "smtp", subject, body, c["email"], attachments=outreach._resume_attachment(job["id"]))
    return outreach.get(aid)


@app.delete("/api/linkedin/{row_id}")
def linkedin_delete(row_id: int):
    db.execute("DELETE FROM li_outreach WHERE id = ?", (row_id,))
    return {"ok": True}


@app.get("/api/outreach-sheet")
def outreach_sheet():
    if not people.SHEET.exists():
        raise HTTPException(404, "No outreach sheet yet. It is created when you approve a job.")
    return FileResponse(people.SHEET, filename="LinkedIn_outreach.xlsx")


@app.post("/api/outreach-sheet/open")
def outreach_sheet_open():
    import os
    if not people.SHEET.exists():
        raise HTTPException(404, "No outreach sheet yet")
    os.startfile(str(people.SHEET))
    return {"opened": str(people.SHEET)}


@app.post("/api/replies/check")
def check_replies():
    return outreach.check_replies()


# ------------------------------------------------------------------ reports

@app.get("/api/report/weekly")
def weekly(days: int = 7):
    return report.weekly(days)


@app.post("/api/report/weekly/export")
def weekly_export(days: int = 7):
    path = report.export_html(days)
    return FileResponse(path, filename=path.name)


@app.get("/api/report/export/{name}")
def report_file(name: str):
    p = config.REPORTS_DIR / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    return (config.WEB_DIR / "index.html").read_text(encoding="utf-8")
