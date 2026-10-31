"""Checks that a listing is genuine and still appears open.

Statuses:
  verified_open  - currently listed on the employer's own career system and the page loads
  likely_open    - fresh listing whose page loads, but not confirmed on the employer system
  unverified     - could not confirm (blocked page, missing date, aggregator only)
  suspicious     - scam signals found
  closed         - removed from the employer system, expired, or the page says it is closed
"""
import re
from urllib.parse import urlparse

from . import config, db
from .sources.common import age_days, session

SCAM_SIGNALS = [
    (r"registration fee|security deposit|processing fee|training fee|pay (?:a |the )?fee|refundable deposit", "asks the candidate for money"),
    (r"telegram", "recruitment over Telegram"),
    (r"whatsapp (?:us|me|your (?:cv|resume))|send (?:your )?(?:cv|resume) (?:on|to) whatsapp", "asks for CV over WhatsApp"),
    (r"earn (?:rs\.?|₹|inr)?\s?\d+[\d,]* (?:per|a) (?:day|week)", "earn-per-day pitch"),
    (r"no interview|direct joining|instant joining", "no-interview hiring"),
    (r"aadhaar|pan card (?:copy|number)|bank (?:account|details)", "asks for identity or bank documents up front"),
]
CLOSED_SIGNALS = r"no longer (?:accepting|available|open)|position (?:has been )?filled|job (?:has )?expired|this job is closed|posting is closed|not accepting applications"
REPOSTERS = r"bebee|jooble|jobrapido|kit ?job|jobleads|talent\.com|jora|whatjobs|jobsora|careerjet|trovit|jobted|grabjobs"
FREE_MAIL =r"@(gmail|yahoo|hotmail|outlook|rediffmail|ymail)\.com"
KNOWN_ATS = ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "myworkdayjobs.com", "amazon.jobs",
             "workable.com", "darwinbox.in", "keka.com", "zohorecruit", "freshteam.com", "icims.com", "taleo.net",
             "successfactors", "jobvite.com", "recruitee.com", "teamtailor.com")


def _host(url):
    h = urlparse(url or "").netloc.lower()
    return h[4:] if h.startswith("www.") else h


def check(job, fetch_page=True):
    s = config.settings()
    notes, flags = [], []
    status = None

    age = age_days(job.get("posted_at"))
    if age is None:
        notes.append("No posting date; freshness cannot be confirmed")
        status = "unverified"
    elif age > s["max_job_age_days"]:
        notes.append(f"Posted {age:.0f} days ago, older than the {s['max_job_age_days']}-day limit")
    else:
        notes.append(f"Posted {age:.1f} days ago")

    if job.get("valid_through") and age_days(job["valid_through"]) not in (None, 0.0):
        notes.append("Application deadline has passed")
        status = "closed"

    desc = (job.get("description") or "").lower()
    for rx, why in SCAM_SIGNALS:
        if re.search(rx, desc):
            flags.append(why)
    if re.search(FREE_MAIL, desc) and job.get("source_type") != "employer":
        flags.append("contact address is a free webmail account")
    if len(desc) < 300:
        notes.append("Very short description")

    apply_host = _host(job.get("apply_url") or job.get("url"))
    domain = (job.get("company_domain") or "").lower()
    on_employer = job.get("source_type") == "employer"
    if on_employer:
        notes.append(f"Listed on {job.get('company')}'s own career system ({job.get('source')})")
    elif domain and (apply_host.endswith(domain) or any(a in apply_host for a in KNOWN_ATS)):
        notes.append(f"Apply link goes to the employer or its ATS ({apply_host})")
        on_employer = True
    elif apply_host:
        notes.append(f"Apply link is on {apply_host}")

    page_ok = None
    if fetch_page and (job.get("url") or job.get("apply_url")):
        url = job.get("url") or job.get("apply_url")
        try:
            r = session.get(url, timeout=s.get("http_timeout_seconds", 20), allow_redirects=True)
            page_ok = r.status_code < 400
            if r.status_code in (404, 410):
                status = "closed"
                notes.append(f"Job page returns HTTP {r.status_code}")
            elif r.status_code >= 400:
                notes.append(f"Job page returned HTTP {r.status_code} (site may block automated checks)")
                page_ok = None
            else:
                final = r.url.rstrip("/")
                if final != url.rstrip("/") and re.search(r"/(careers|jobs)/?$|error|not-?found", final, re.I):
                    status = "closed"
                    notes.append(f"Job page redirects to {final}")
                elif re.search(CLOSED_SIGNALS, r.text[:200000], re.I):
                    status = "closed"
                    notes.append("Job page says the position is closed")
                else:
                    notes.append("Job page loads")
        except Exception as e:
            notes.append(f"Could not load job page ({type(e).__name__})")

    repost = re.search(REPOSTERS, (job.get("publisher") or "") + " " + apply_host, re.I)
    if repost and not on_employer:
        notes.append(f"Reposted by {repost.group(0)}: find the original posting (company site, LinkedIn or Naukri) before applying")

    if flags:
        status = "suspicious"
    if status is None:
        fresh = age is not None and age <= s["max_job_age_days"]
        if on_employer and fresh and page_ok is not False:
            status = "verified_open" if job.get("source_type") == "employer" else "likely_open"
        elif fresh and page_ok and not repost:
            status = "likely_open"
        else:
            status = "unverified"
    return {"status": status, "notes": notes, "flags": flags}


def verify_job(job_id, fetch_page=True):
    import json
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not job:
        return None
    res = check(job, fetch_page=fetch_page)
    db.update("jobs", "id", job_id, {"verification_status": res["status"],
                                    "verification_notes": json.dumps(res), "verified_at": db.now()})
    db.log_event(job_id, "verified", res["status"])
    return res
