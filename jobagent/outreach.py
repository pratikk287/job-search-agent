"""Outreach drafts, the approval queue, and execution of approved actions.

Nothing leaves the machine unless the action is in status 'approved' and the user then presses Execute.
Editing an approved action sends it back to 'draft'. LinkedIn messages are never automated: the app copies
the text and opens the profile; the user sends it and marks it sent. Applications are pre-filled in a visible
browser and the user clicks the employer's Submit button.
"""
import imaplib
import json
import re
import smtplib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid, parsedate_to_datetime
from pathlib import Path

from . import config, db, llm, resumes
from .keywords import FUNCTIONS

LINKEDIN_NOTE_LIMIT = 200   # free-account connection note limit


def _short(bullet, limit=150):
    b = bullet.rstrip(".")
    for sep in ("; ", ", enabling", ", leveraging", ", defining", ", guiding", ", with ", ", alongside"):
        if sep in b and len(b.split(sep)[0]) >= 45:
            b = b.split(sep)[0]
            break
    if len(b) > limit:
        b = b[:limit].rsplit(" ", 1)[0]
    return b[0].lower() + b[1:]


def _first(name):
    return (name or "").split()[0] if name else None


def _ctx(job, contact):
    key = job.get("resume_key") or "A"
    from .documents import clean_title
    job["title"] = clean_title(job["title"], job["company"])
    r = resumes.load(key)
    top = resumes.achievements(key, job, n=2)
    prof = config.profile()
    return {
        "key": key, "prof": prof, "first": _first(contact.get("name")) if contact else None,
        "fn": FUNCTIONS.get(job.get("function") or "", {}).get("label", "marketing"),
        "a1": _short(top[0]["bullet"]) if top else "", "a2": _short(top[1]["bullet"]) if len(top) > 1 else "",
        "title_now": prof.get("current_title") or r["experience"][0]["title"],
        "employer": r["experience"][0]["company"].split(" (")[0],
    }


def draft_email(job, contact, kind="email"):
    c = _ctx(job, contact)
    hi = f"Hi {c['first']}," if c["first"] else "Hello,"
    is_hm = contact and contact.get("role_type") in ("hiring_manager", "department_head")
    subject = f"{job['title']} at {job['company']} - {c['prof']['name']}"
    if kind == "followup_email":
        subject = f"Re: {subject}"
        body = (f"{hi}\n\nFollowing up on my note about the {job['title']} role. I remain very interested and would be glad "
                f"to share more on how I {c['a1']}.\n\nIf someone else on the team is the right person for this, I would "
                f"appreciate a pointer.\n\nThanks,\n{c['prof']['name']}\n{c['prof']['phone']}")
        return subject, body
    opener = (f"I have applied for the {job['title']} role and wanted to reach out to you directly, since the role sits in "
              f"your team." if is_hm else
              f"I am interested in the {job['title']} role{' in ' + job['city'] if job.get('city') and job['city'] != 'Remote' else ''} "
              f"and wanted to share my profile with you.")
    cta = ("I'd value any pointers on what the team is looking for in this role, and I'm happy to share more of my work if useful."
           if is_hm else "If you need anything else from me for the application, I'll be glad to send it.")
    body = (f"{hi}\n\n{opener}\n\nI work as {c['title_now']} at {c['employer']} and have {c['prof']['total_experience_years']} "
            f"years of experience. Two things that are relevant here:\n"
            f"- I {c['a1']}\n" + (f"- I {c['a2']}\n" if c["a2"] else "") +
            + (f"\n{config.pitch()['extras']}\n" if config.pitch()["extras"] else "") + "\n"
            f"My resume is attached. {cta}\n\n"
            f"Regards,\n{c['prof']['name']}\n{c['prof']['phone']} | {c['prof']['email']}\n{c['prof'].get('linkedin_url', '')}").strip()
    return subject, body


def draft_linkedin_note(job, contact):
    c = _ctx(job, contact)
    hi = f"Hi {c['first']}, " if c["first"] else "Hi, "
    options = [
        f"{hi}I'm applying for {job['title']} at {job['company']}. I work in {c['fn'].lower()} at {c['employer']} "
        f"and would value connecting.",
        f"{hi}I'm applying for the {job['company']} {c['fn']} role and would value connecting.",
        f"{hi}I'd value connecting about roles at {job['company']}.",
    ]
    return next(o for o in options if len(o) <= LINKEDIN_NOTE_LIMIT)


def draft_linkedin_message(job, contact):
    c = _ctx(job, contact)
    hi = f"Hi {c['first']}," if c["first"] else "Hi,"
    return (f"{hi} thanks for connecting. I've applied for the {job['title']} role at {job['company']}. "
            f"At {c['employer']} I {c['a1']}. I'd be glad to share more, or be pointed to the right person on the team. "
            f"Thank you!\n{c['prof']['name']}")


def _polish(text, job, kind, limit=None):
    """Optional Claude rewrite; rejected if it adds numbers or breaks the length limit."""
    if not llm.available():
        return text, "template"
    key = job.get("resume_key") or "A"
    out = llm.generate(f"Improve this {kind} for a job outreach. Keep it short, specific and polite; keep all facts; "
                       f"add none.{f' Hard limit {limit} characters.' if limit else ''}\n\nRESUME:\n"
                       f"{resumes.load(key)['full_text']}\n\nJOB: {job['title']} at {job['company']}\n\nDRAFT:\n{text}",
                       max_tokens=1500)
    allowed = resumes.resume_numbers(key) | set(re.findall(r"\d+(?:\.\d+)?", (job.get("description") or "") + job["title"]))
    allowed.add(str(config.profile()["total_experience_years"]))
    if not out or llm.invented_numbers(out, allowed) or (limit and len(out) > limit):
        return text, "template"
    return out, "claude"


# ------------------------------------------------------------------ queue

def _create(kind, job_id, contact_id, channel, subject, body, recipient, attachments=None, due_at=None, parent=None):
    return db.insert("actions", {
        "job_id": job_id, "contact_id": contact_id, "kind": kind, "channel": channel, "subject": subject,
        "body": body, "recipient": recipient, "attachments": json.dumps(attachments or []), "status": "draft",
        "due_at": due_at, "parent_action_id": parent, "created_at": db.now(), "updated_at": db.now()})


def _resume_attachment(job_id):
    doc = db.one("SELECT path FROM documents WHERE job_id = ? AND kind = 'resume_pdf'", (job_id,))
    if doc:
        return [doc["path"]]
    job = db.one("SELECT resume_key FROM jobs WHERE id = ?", (job_id,))
    return [str(config.ROOT / config.profile()["resumes"][job["resume_key"] or "A"]["file"])]


def create_drafts(job_id, contact_id):
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    contact = db.one("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    made = []
    if contact.get("email"):
        subject, body = draft_email(job, contact)
        body, _ = _polish(body, job, "email")
        made.append(_create("email", job_id, contact_id, "smtp", subject, body, contact["email"],
                            attachments=_resume_attachment(job_id)))
    if contact.get("linkedin_url") or contact.get("name"):
        note, _ = _polish(draft_linkedin_note(job, contact), job, "LinkedIn connection note", LINKEDIN_NOTE_LIMIT)
        made.append(_create("linkedin_note", job_id, contact_id, "manual", None, note, contact.get("linkedin_url")))
        msg, _ = _polish(draft_linkedin_message(job, contact), job, "LinkedIn message", 1000)
        made.append(_create("linkedin_message", job_id, contact_id, "manual", None, msg, contact.get("linkedin_url")))
    db.log_event(job_id, "drafts_created", {"contact_id": contact_id, "actions": made})
    return [get(a) for a in made]


def create_application(job_id):
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    existing = db.one("SELECT id FROM actions WHERE job_id = ? AND kind = 'application' AND status IN ('draft','approved','executing')", (job_id,))
    if existing:
        return get(existing["id"])
    docs = {d["kind"]: d["path"] for d in db.query("SELECT kind, path FROM documents WHERE job_id = ?", (job_id,))}
    attachments = [docs.get("resume_pdf") or _resume_attachment(job_id)[0]]
    if docs.get("cover_letter_txt"):
        attachments.append(docs["cover_letter_txt"])
    summary = (f"Open {job['apply_url'] or job['url']} in a browser, fill your details and upload "
               f"{Path(attachments[0]).name}. You review the form and press the employer's Submit button yourself.")
    aid = _create("application", job_id, None, "browser_prefill", f"Apply: {job['title']} at {job['company']}",
                  summary, job["apply_url"] or job["url"], attachments=attachments)
    return get(aid)


def get(action_id):
    a = db.one("SELECT * FROM actions WHERE id = ?", (action_id,))
    if a:
        a["attachments"] = json.loads(a["attachments"] or "[]")
        a["result"] = json.loads(a["result"]) if a.get("result") and a["result"].startswith("{") else a.get("result")
    return a


def edit(action_id, fields):
    a = get(action_id)
    if a["status"] in ("done", "executing"):
        raise ValueError("Already executed; cannot edit")
    allowed = {k: v for k, v in fields.items() if k in ("subject", "body", "recipient")}
    if a["kind"] == "linkedin_note" and len(allowed.get("body", a["body"]) or "") > LINKEDIN_NOTE_LIMIT:
        raise ValueError(f"LinkedIn notes are limited to {LINKEDIN_NOTE_LIMIT} characters")
    allowed.update({"status": "draft", "approved_at": None, "updated_at": db.now()})   # edits need re-approval
    db.update("actions", "id", action_id, allowed)
    return get(action_id)


def approve(action_id):
    a = get(action_id)
    if a["status"] not in ("draft", "failed"):
        raise ValueError(f"Cannot approve an action in status {a['status']}")
    if a["kind"] in ("email", "followup_email") and not a.get("recipient"):
        raise ValueError("No recipient email")
    if a["kind"] in ("email", "followup_email"):
        from . import contacts
        ok, why = contacts.email_trust(a["recipient"], db.one("SELECT * FROM contacts WHERE id = ?", (a["contact_id"],)) if a.get("contact_id") else None)
        if not ok:
            raise ValueError("Not approved: " + why)
    db.update("actions", "id", action_id, {"status": "approved", "approved_at": db.now(), "updated_at": db.now()})
    db.log_event(a["job_id"], "action_approved", {"action_id": action_id, "kind": a["kind"]})
    return get(action_id)


def reject(action_id):
    db.update("actions", "id", action_id, {"status": "rejected", "updated_at": db.now()})
    return get(action_id)


def _emails_sent_today():
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat(timespec="seconds")
    return db.one("SELECT COUNT(*) n FROM actions WHERE kind IN ('email','followup_email') AND status = 'done' "
                  "AND channel = 'smtp' AND executed_at >= ?", (start,))["n"]


def smtp_connect(host, port, user, pwd):
    """Logged-in SMTP connection. Tries STARTTLS on 587, then SSL on 465 (some networks drop one of them).
    A rejected password is raised immediately; only connection drops fall through to the next attempt."""
    last = None
    for p, use_ssl in ((port, port == 465), (465, True), (587, False)):
        try:
            s = smtplib.SMTP_SSL(host, p, timeout=30) if use_ssl else smtplib.SMTP(host, p, timeout=30)
            s.ehlo()
            if not use_ssl:
                s.starttls()
                s.ehlo()
            # One auth mechanism only: Gmail hangs up after a rejected attempt, and smtplib.login() would then try a
            # second mechanism and report "server disconnected" instead of "wrong password".
            s.user, s.password = user, pwd
            s.auth("PLAIN", s.auth_plain)
            return s
        except smtplib.SMTPAuthenticationError:
            raise
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, OSError) as e:
            last = e
    raise last


def send_email(a):
    user, pwd = config.secret("SMTP_USER"), config.secret("SMTP_PASSWORD")
    if not (user and pwd):
        raise RuntimeError("SMTP is not configured (.env SMTP_USER / SMTP_PASSWORD)")
    cap = config.settings()["max_emails_per_day"]
    if _emails_sent_today() >= cap:
        raise RuntimeError(f"Daily email limit of {cap} reached")
    msg = EmailMessage()
    msg["From"] = f"{config.secret('FROM_NAME') or config.profile()['name']} <{user}>"
    msg["To"] = a["recipient"]
    msg["Subject"] = a["subject"]
    msg["Message-ID"] = make_msgid(domain=user.split("@")[-1])
    msg.set_content(a["body"])
    for path in a["attachments"]:
        p = Path(path)
        if p.exists():
            sub = {"pdf": ("application", "pdf"), "docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
                   "txt": ("text", "plain")}.get(p.suffix.lstrip(".").lower(), ("application", "octet-stream"))
            msg.add_attachment(p.read_bytes(), maintype=sub[0], subtype=sub[1], filename=p.name)
    s = smtp_connect(config.secret("SMTP_HOST") or "smtp.gmail.com", int(config.secret("SMTP_PORT") or 587), user, pwd)
    try:
        s.send_message(msg)
    finally:
        s.quit()
    return {"message_id": msg["Message-ID"], "sent_from": user}


def execute(action_id):
    """Run one approved action. Returns the updated action."""
    a = get(action_id)
    if a["status"] != "approved":
        raise ValueError("Only approved actions can be executed")
    job = db.one("SELECT * FROM jobs WHERE id = ?", (a["job_id"],))
    try:
        if a["kind"] in ("email", "followup_email"):
            db.update("actions", "id", action_id, {"status": "executing"})
            res = send_email(a)
            db.update("actions", "id", action_id, {"status": "done", "executed_at": db.now(), "result": json.dumps(res)})
            db.log_event(a["job_id"], "email_sent", {"to": a["recipient"], "action_id": action_id})
            if a["kind"] == "email":
                schedule_followup(a, job)
        elif a["kind"] in ("linkedin_note", "linkedin_message"):
            db.update("actions", "id", action_id, {"status": "executing", "result": json.dumps(
                {"instructions": "Text copied. Paste it in LinkedIn, send it, then press 'Mark sent'.",
                 "open_url": a.get("recipient")})})
        elif a["kind"] == "application":
            log = config.DATA_DIR / f"autofill_{action_id}.json"
            subprocess.Popen([sys.executable, "-m", "jobagent.autofill", str(action_id)], cwd=str(config.ROOT),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            db.update("actions", "id", action_id, {"status": "executing", "result": json.dumps(
                {"instructions": "A browser window is opening with the form pre-filled. Check every field, answer any "
                                 "extra questions, press Submit on the employer's page, then press 'Mark submitted' here.",
                 "log": str(log)})})
    except Exception as e:
        db.update("actions", "id", action_id, {"status": "failed", "result": json.dumps({"error": str(e)})})
        db.log_event(a["job_id"], "action_failed", {"action_id": action_id, "error": str(e)})
    return get(action_id)


def mark_done(action_id):
    """User confirms a manual step happened (LinkedIn sent, application submitted, email sent from own client)."""
    a = get(action_id)
    if a["status"] not in ("approved", "executing", "failed"):
        raise ValueError("Approve the action first")
    db.update("actions", "id", action_id, {"status": "done", "executed_at": db.now()})
    job = db.one("SELECT * FROM jobs WHERE id = ?", (a["job_id"],))
    if a["kind"] == "application":
        db.update("jobs", "id", a["job_id"], {"status": "applied", "applied_at": db.now()})
        db.log_event(a["job_id"], "applied", {"action_id": action_id})
    else:
        db.log_event(a["job_id"], f"{a['kind']}_sent", {"action_id": action_id})
        if a["kind"] == "email":
            schedule_followup(a, job)
    return get(action_id)


def schedule_followup(a, job):
    days = config.settings()["followup_after_days"]
    due = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")
    contact = db.one("SELECT * FROM contacts WHERE id = ?", (a["contact_id"],)) if a.get("contact_id") else None
    subject, body = draft_email(job, contact, kind="followup_email")
    _create("followup_email", a["job_id"], a.get("contact_id"), "smtp", subject, body, a["recipient"],
            due_at=due, parent=a["id"])


def test_gmail_login(user, pwd):
    """Log in to Gmail SMTP and IMAP without sending anything. Returns (ok, message)."""
    try:
        s = smtp_connect("smtp.gmail.com", 587, user, pwd)
        s.quit()
    except smtplib.SMTPAuthenticationError:
        return False, ("Gmail rejected the login. Use a 16-letter app password from myaccount.google.com/apppasswords "
                       "(not your normal Gmail password), and check the address is the same account.")
    except Exception as e:
        return False, f"Could not reach Gmail ({type(e).__name__}: {str(e)[:120] or 'connection closed'}). Check your internet or VPN/antivirus and try again."
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com") as M:
            M.login(user, pwd)
        return True, "Connected. Sending and reply tracking both work."
    except Exception:
        return True, "Connected for sending. Reply tracking could not log in to your inbox (IMAP may be off in Gmail settings)."


def check_bounces():
    """Find delivery-failure notices for emails we sent; mark them bounced and cancel their follow-ups."""
    user, pwd, host = config.secret("SMTP_USER"), config.secret("SMTP_PASSWORD"), config.secret("IMAP_HOST")
    if not (user and pwd and host):
        return {"bounced": 0}
    sent = db.query("SELECT * FROM actions WHERE kind IN ('email','followup_email') AND status = 'done' "
                    "AND (result IS NULL OR result NOT LIKE '%\"bounced\"%')")
    if not sent:
        return {"bounced": 0}
    since = min(datetime.fromisoformat(a["executed_at"]) for a in sent).strftime("%d-%b-%Y")
    bounced = set()
    with imaplib.IMAP4_SSL(host) as M:
        M.login(user, pwd)
        M.select("INBOX", readonly=True)
        typ, data = M.search(None, '(OR FROM "mailer-daemon" FROM "postmaster")', "SINCE", since)
        for i in (data[0].split() if typ == "OK" and data and data[0] else []):
            typ, msg = M.fetch(i, "(BODY.PEEK[TEXT]<0.6000>)")
            text = b"".join(x[1] for x in msg if isinstance(x, tuple)).decode(errors="ignore").lower()
            for a in sent:
                if a["recipient"].lower() in text:
                    bounced.add(a["id"])
    for a in sent:
        if a["id"] in bounced:
            db.update("actions", "id", a["id"], {"result": json.dumps({"bounced": True, "note": "Bounced: this address does not exist. The email was a guess."})})
            db.execute("UPDATE contacts SET email_status = 'bounced' WHERE email = ?", (a["recipient"],))
            from . import contacts as _contacts
            who = db.one("SELECT name, email_status FROM contacts WHERE lower(email) = ? LIMIT 1", (a["recipient"].lower(),)) or {}
            if who.get("email_status") in (None, "bounced", "pattern_guess"):
                _contacts.record_bounced_format(a["recipient"], who.get("name"))
            db.execute("UPDATE actions SET status = 'rejected', result = 'Cancelled: the address bounced' "
                       "WHERE kind = 'followup_email' AND status = 'draft' AND recipient = ?", (a["recipient"],))
            db.log_event(a["job_id"], "email_bounced", {"to": a["recipient"]})
    return {"bounced": len(bounced)}


def check_replies():
    """Mark sent emails as replied when the recipient has written back (IMAP)."""
    user, pwd, host = config.secret("SMTP_USER"), config.secret("SMTP_PASSWORD"), config.secret("IMAP_HOST")
    if not (user and pwd and host):
        return {"error": "IMAP not configured"}
    check_bounces()
    sent = db.query("SELECT * FROM actions WHERE kind IN ('email','followup_email') AND status = 'done' AND replied_at IS NULL "
                    "AND (result IS NULL OR result NOT LIKE '%\"bounced\"%')")
    found = 0
    with imaplib.IMAP4_SSL(host) as M:
        M.login(user, pwd)
        M.select("INBOX", readonly=True)
        for a in sent:
            since = datetime.fromisoformat(a["executed_at"]).strftime("%d-%b-%Y")
            typ, data = M.search(None, "FROM", f'"{a["recipient"]}"', "SINCE", since)
            ids = data[0].split() if typ == "OK" and data and data[0] else []
            if not ids:
                continue
            typ, msg = M.fetch(ids[-1], "(BODY.PEEK[HEADER.FIELDS (DATE SUBJECT)])")
            header = msg[0][1].decode(errors="ignore") if msg and msg[0] else ""
            m = re.search(r"Date: (.+)", header)
            when = parsedate_to_datetime(m.group(1).strip()).isoformat() if m else db.now()
            db.update("actions", "id", a["id"], {"replied_at": when})
            # a reply cancels the follow-ups waiting on this thread
            db.execute("UPDATE actions SET status = 'rejected', result = 'Cancelled: recipient replied' "
                       "WHERE kind = 'followup_email' AND status = 'draft' AND recipient = ? AND job_id = ?",
                       (a["recipient"], a["job_id"]))
            db.log_event(a["job_id"], "reply_received", {"from": a["recipient"]})
            found += 1
    return {"checked": len(sent), "replies": found}


def mark_replied(action_id):
    a = get(action_id)
    db.update("actions", "id", action_id, {"replied_at": db.now()})
    db.execute("UPDATE actions SET status = 'rejected', result = 'Cancelled: recipient replied' "
               "WHERE kind = 'followup_email' AND status = 'draft' AND parent_action_id = ?", (action_id,))
    db.log_event(a["job_id"], "reply_received", {"action_id": action_id})
    return get(action_id)
