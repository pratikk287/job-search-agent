"""Find the HR contact and the hiring team's people for a job, write a LinkedIn outreach sheet, queue email drafts.

Profiles come from ordinary public web search results (the same snippets Google shows: name, title, company, link).
LinkedIn itself is never scraped or logged in to; the messages are sent by you by hand.
"""
import re
import threading
from datetime import datetime
from pathlib import Path

from . import config, contacts, db, outreach, resumes
from .keywords import FUNCTIONS
from .sources import firecrawl

SHEET = config.DATA_DIR / "outreach.xlsx"
_sheet_lock = threading.Lock()

FUNCTION_QUERY = {
    "product_marketing": '"product marketing"', "growth_marketing": '(growth OR "performance marketing")',
    "product_management": '("product manager" OR product)', "marketplace_category": '(category OR marketplace OR seller)',
    "ecommerce_commercial": '(ecommerce OR "e-commerce" OR commercial)', "ai_product": '("AI" product)',
    "tech_business": '(strategy OR partnerships)',
}
FUNCTION_WORDS = {
    "product_marketing": r"product marketing|go-to-market|gtm|marketing", "growth_marketing": r"growth|performance|marketing|acquisition",
    "product_management": r"product", "marketplace_category": r"category|marketplace|seller|merchandis",
    "ecommerce_commercial": r"e-?commerce|commercial|business", "ai_product": r"product|\bai\b",
    "tech_business": r"strategy|partnership|business",
}


def _clean_name(raw):
    name = re.sub(r"\(.*?\)|,.*$|[|•·].*$", "", raw or "").strip()
    name = re.sub(r"\b(MBA|PMP|CFA|Dr\.?|CA|PhD)\b", "", name).strip()
    return name if re.match(r"^[A-Za-z][A-Za-z .'\-]{2,38}$", name) and len(name.split()) >= 2 else None


def parse_profile(result, company):
    """One web-search hit -> {name, title, linkedin_url} if it is a person who works at `company`."""
    m = re.search(r"linkedin\.com/in/([A-Za-z0-9\-_%]+)", result.get("url", ""))
    if not m:
        return None
    title, desc = result.get("title") or "", result.get("description") or ""
    head = re.split(r"\s+[-–|]\s+", title)
    name = _clean_name(head[0])
    if not name:
        return None
    first_word = re.sub(r"[^a-z0-9 ]", "", company.lower()).split()[0]
    blob = f"{title} {desc[:200]}".lower()
    if first_word not in re.sub(r"[^a-z0-9 ]", "", blob):
        return None
    # "Ex Meesho", "former Meesho", "previously at Meesho" in the headline: not a current employee
    if re.search(r"\b(ex|former|formerly|previously|alumn\w*)\b[\s\-:@|]*(at\s+)?" + re.escape(first_word), blob):
        return None
    # the company must appear in the position part of the headline ("... at Meesho"), not only somewhere in the snippet
    position = " ".join(head[1:3]).lower()
    if first_word not in position and first_word not in desc[:80].lower():
        return None
    role = re.sub(r"\s+(at|@)\s+.*$", "", head[1] if len(head) > 1 else "", flags=re.I).strip()
    if not role or role.lower().startswith(first_word) or role.lower() == "linkedin":
        role = re.sub(r"\s+(at|@)\s+.*$", "", desc.split(" · ")[0][:80], flags=re.I).strip()
    return {"name": name, "title": role[:90], "linkedin_url": f"https://www.linkedin.com/in/{m.group(1)}", "_text": f"{title} {desc[:160]}".lower()}


def _hr_score(p):
    t = p["_text"]
    if not re.search(r"talent|recruit|\bhr\b|human resources|people (partner|operations)|hiring", t):
        return -1
    s = 3 if re.search(r"talent acquisition|recruit", p["title"].lower()) else 1
    s += 1 if re.search(r"manager|partner|lead|specialist", p["title"].lower()) else 0
    s -= 1 if re.search(r"\bvp\b|vice president|chief|\bcho\b|head of (hr|human)", p["title"].lower()) else 0
    return s


def _dept_score(p, function):
    t = p["title"].lower()
    if not re.search(FUNCTION_WORDS.get(function, "marketing"), t) or re.search(r"talent|recruit|\bhr\b|intern\b|trainee", t):
        return -1
    s = 3 if re.search(r"director|head|vp|vice president|lead|group", t) else 2 if re.search(r"senior manager|manager", t) else 0
    return s


GENERIC = {"manager", "senior", "associate", "assistant", "head", "lead", "director", "specialist", "executive", "india", "team",
           "the", "and", "for", "with", "role", "global", "group", "principal", "analyst", "officer", "vice", "president"}


def _relevance_bonus(p, job):
    """Extra points for people whose headline mentions this role's own words or city (e.g. 'Sports', 'Pricing', 'Mumbai')."""
    words = {w for w in re.findall(r"[a-z]{4,}", re.sub(r"[\(\[].*?[\)\]]", "", job["title"].lower())) if w not in GENERIC}
    text = p["_text"]
    bonus = min(2, sum(1 for w in words if w in text))
    city = (job.get("city") or "").lower()
    return bonus + (1 if city and city not in ("remote", "india") and city in text else 0)


def people_per_job():
    return min(3, max(2, int(config.settings().get("people_per_job", 3))))


def find_people(job, limit=None):
    """The 2-3 most relevant people for a job: the best HR contact first, then the closest members of the hiring team."""
    limit = limit or people_per_job()
    company = job["company"]
    where = job.get("city") if job.get("city") not in (None, "Remote") else "India"
    fn = job.get("function") or "product_marketing"
    hr_hits = firecrawl.search(f'site:linkedin.com/in {company} {where} ("talent acquisition" OR recruiter OR "HR business partner")')
    dept_hits = firecrawl.search(f'site:linkedin.com/in {company} India {FUNCTION_QUERY.get(fn, "marketing")} (head OR director OR lead OR manager)')
    hr = sorted([p for p in (parse_profile(r, company) for r in hr_hits) if p and _hr_score(p) > 0],
                key=lambda p: -(_hr_score(p) + _relevance_bonus(p, job)))
    dept = sorted([p for p in (parse_profile(r, company) for r in dept_hits) if p and _dept_score(p, fn) > 0],
                  key=lambda p: -(_dept_score(p, fn) + _relevance_bonus(p, job)))
    chosen = [(hr[0], "recruiter")] if hr else []                       # one HR contact
    for p in dept:                                                      # then the most relevant hiring-team people
        if len(chosen) < limit:
            chosen.append((p, "department_head"))
    for p in hr[1:]:                                                    # not enough team people: a second HR contact
        if len(chosen) < min(limit, 2):
            chosen.append((p, "recruiter"))
    seen, out = set(), []
    for p, kind in chosen[:limit]:
        if p["linkedin_url"] not in seen:
            seen.add(p["linkedin_url"])
            out.append({**p, "role_type": kind})
    return out


# ------------------------------------------------------------------ messages

def short_role(job):
    """'Assistant Manager / Manager – Product Marketing (Mumbai)' stays whole; location tails and company names are dropped."""
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", job["title"])
    parts = [p.strip() for p in t.split(",")]
    kept = []
    for p in parts:
        if not p or job["company"].lower().split()[0] in p.lower():
            break
        kept.append(p)
    return (", ".join(kept[:2]) or t)[:60].strip(" -–")


def linkedin_note(job, person):
    """Connection note, <= 200 characters, personalised to the role and the person's side of the hiring process."""
    first = (person.get("name") or "").split()[0]
    role = short_role(job)
    pt = config.pitch()
    if person["role_type"] == "recruiter":
        options = [f"Hi {first}, I've applied for the {role} role at {job['company']}. {pt['hr']} Would be glad to connect and share my profile.",
                   f"Hi {first}, I've applied for {role} at {job['company']}. Would love to connect and be on your radar for it.",
                   f"Hi {first}, applied for {role} at {job['company']}. Happy to connect."]
    else:
        options = [f"Hi {first}, I've applied for the {role} role in your team at {job['company']}. {pt['team']} Would value connecting and learning how your team works.",
                   f"Hi {first}, I've applied for {role} at {job['company']}. My background looks relevant to your team. Would value connecting.",
                   f"Hi {first}, applied for {role} at {job['company']}. Would value connecting."]
    return next(o for o in options if len(o) <= 200)


def follow_up_message(job, person):
    first = (person.get("name") or "").split()[0]
    key = job.get("resume_key") or "A"
    a = outreach._short(resumes.achievements(key, job, n=1)[0]["bullet"]) if resumes.achievements(key, job, n=1) else ""
    role = short_role(job)
    if person["role_type"] == "recruiter":
        return (f"Hi {first}, thanks for connecting. I've applied for the {role} role at {job['company']}"
                f" and wanted to put a face to the application. In my current role I {a}. "
                f"If it helps, I can share my resume or answer anything about my background. Thank you!")
    return (f"Hi {first}, thanks for connecting. I've applied for the {role} role in your team at {job['company']}. "
            f"In my current role at {config.pitch()['employer']} I {a}. I'd be glad to share how that maps to what your team needs "
            f"and to answer anything about my background. Thank you!")


# ------------------------------------------------------------------ sheet

COLUMNS = ["Date", "Company", "Job", "Job link", "Person", "Their title", "Who", "LinkedIn profile",
           "Connection note (<=200)", "Message after they accept", "Guessed email (unverified)", "Note sent?", "Message sent?", "Reply?"]


def append_to_sheet(job, people):
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    with _sheet_lock:
        if SHEET.exists():
            wb = load_workbook(SHEET)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "LinkedIn outreach"
            ws.append(COLUMNS)
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="1F3A5F")
                c.alignment = Alignment(vertical="center", wrap_text=True)
            for col, w in zip("ABCDEFGHIJKLMN", (11, 20, 30, 28, 22, 30, 13, 34, 52, 62, 30, 11, 13, 10)):
                ws.column_dimensions[col].width = w
            ws.freeze_panes = "A2"
            dv = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
            ws.add_data_validation(dv)
            dv.add("L2:N2000")
        have = {ws.cell(row=r, column=8).value for r in range(2, ws.max_row + 1)}
        have_jobs = {(ws.cell(row=r, column=2).value, ws.cell(row=r, column=3).value, ws.cell(row=r, column=8).value) for r in range(2, ws.max_row + 1)}
        added = 0
        for p in people:
            if (job["company"], job["title"], p["linkedin_url"]) in have_jobs:
                continue
            ws.append([datetime.now().strftime("%d %b %Y"), job["company"], job["title"], job.get("url") or "", p["name"], p["title"],
                       "HR / recruiter" if p["role_type"] == "recruiter" else "Hiring team", p["linkedin_url"],
                       linkedin_note(job, p), follow_up_message(job, p), p.get("email") or "", "", "", ""])
            row = ws.max_row
            ws.cell(row=row, column=8).hyperlink = p["linkedin_url"]
            ws.cell(row=row, column=8).font = Font(color="0563C1", underline="single")
            if job.get("url"):
                ws.cell(row=row, column=4).hyperlink = job["url"]
            for c in ws[row]:
                c.alignment = Alignment(vertical="top", wrap_text=True)
            added += 1
        try:
            wb.save(SHEET)
            return str(SHEET), added
        except PermissionError:      # the file is open in Excel
            alt = SHEET.with_name(f"outreach_{datetime.now().strftime('%H%M%S')}.xlsx")
            wb.save(alt)
            return str(alt), added


# ------------------------------------------------------------------ pipeline

def research_job(job_id, queue_emails=False):
    """People search -> contacts -> sheet rows -> (guessed) email drafts waiting in Approvals."""
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    result = {"people": [], "sheet": None, "added_rows": 0, "email_drafts": [], "notes": []}
    try:
        people = find_people(job)
    except Exception as e:
        result["notes"].append(f"People search failed: {type(e).__name__}: {str(e)[:120]}")
        db.log_event(job_id, "outreach_research_failed", result["notes"][-1])
        return result
    domain = (job.get("company_domain") or next((c.get("domain") for c in config.companies()
                                                  if c["name"].lower() == job["company"].lower()), None)
              or contacts.guess_domain(job["company"]))
    domain = contacts.base_domain(domain) if domain else domain
    if domain and not job.get("company_domain"):
        db.update("jobs", "id", job_id, {"company_domain": domain})
        result["notes"].append(f"Company website taken as {domain}; check it before sending guessed emails.")
    for p in people:
        email, status = contacts.guess_email(p["name"], domain) if domain else (None, None)
        if email and not contacts.domain_accepts_mail(domain):
            email, status = None, None
        p["email"] = email
        cid = contacts._save(job_id, job["company"], {
            "name": p["name"], "title": p["title"], "role_type": p["role_type"], "email": email,
            "email_status": status or "unknown", "email_confidence": 30 if email else None,
            "linkedin_url": p["linkedin_url"], "source": "public web search (LinkedIn profile)", "source_url": p["linkedin_url"]})
        p["contact_id"] = cid
    result["people"] = [{k: v for k, v in p.items() if not k.startswith("_")} for p in people]
    if not people:
        result["notes"].append("No matching public LinkedIn profiles found for this company.")
        return result
    for p in people:
        try:
            db.insert("li_outreach", {
                "job_id": job_id, "contact_id": p.get("contact_id"), "company": job["company"], "job_title": job["title"],
                "job_url": job.get("url"), "name": p["name"], "title": p["title"],
                "who": "hr" if p["role_type"] == "recruiter" else "team", "linkedin_url": p["linkedin_url"],
                "note": linkedin_note(job, p), "message": follow_up_message(job, p), "email_guess": p.get("email"),
                "created_at": db.now()})
        except Exception:
            pass          # already listed for this job
    result["sheet"], result["added_rows"] = append_to_sheet(job, people)
    if queue_emails:
        for p in people:
            if not p.get("email"):
                continue
            exists = db.one("SELECT id FROM actions WHERE job_id = ? AND contact_id = ? AND kind = 'email'", (job_id, p["contact_id"]))
            if exists:
                continue
            contact = db.one("SELECT * FROM contacts WHERE id = ?", (p["contact_id"],))
            subject, body = outreach.draft_email(job, contact)
            body, _ = outreach._polish(body, job, "email")
            aid = outreach._create("email", job_id, p["contact_id"], "smtp", subject, body, p["email"],
                                   attachments=outreach._resume_attachment(job_id))
            result["email_drafts"].append(aid)
        if result["email_drafts"]:
            result["notes"].append("Email addresses are guesses from the company's usual pattern; some may bounce. Review before approving.")
    db.log_event(job_id, "outreach_researched", {"people": len(people), "rows": result["added_rows"], "emails": len(result["email_drafts"])})
    return result


def research_in_background(job_id):
    threading.Thread(target=research_job, args=(job_id,), daemon=True).start()
