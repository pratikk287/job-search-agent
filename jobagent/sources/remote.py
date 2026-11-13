"""International remote jobs: companies and agencies in the US, UK, Europe and APAC hiring remote people who live in India.

All four boards are free and need no key. A listing is kept only when it is open to India: no location limit
("worldwide", "anywhere"), or a limit that includes India, APAC or Asia. US-only or UK-only remote roles are dropped,
because they need the right to work in that country.
"""
import re

from ..keywords import is_relevant_title
from .common import get_json, html_to_text, parse_date

OPEN_TO_INDIA = re.compile(r"worldwide|anywhere|global|\bindia\b|\bapac\b|asia|\bremote\b", re.I)
SOURCES = ("himalayas", "remotive", "jobicy", "remoteok")
LOCATION = "Remote (international, open to India)"


def open_to_india(where):
    """`where` is a list of allowed countries/regions or a free-text location. Empty means no limit."""
    items = [w for w in (where if isinstance(where, list) else re.split(r"[,;/|]", where or "")) if str(w).strip()]
    return not items or any(OPEN_TO_INDIA.search(str(w)) for w in items)


def _salary(lo, hi, currency, period="year"):
    if not (lo or hi):
        return None
    fmt = lambda v: f"{round(v / 1000)}k" if v and v >= 1000 else str(v or "")
    return f"{currency or 'USD'} {fmt(lo)}{'-' + fmt(hi) if hi and hi != lo else ''} per {period or 'year'}"


def _job(source, ext_id, company, title, url, description, posted_at, where, salary_text=None, publisher=None):
    return {
        "source": source, "source_type": "aggregator", "publisher": publisher or f"{source.title()} (remote)",
        "external_id": str(ext_id), "company": (company or "").strip() or "Unknown", "company_domain": None,
        "title": (title or "").strip(), "location": LOCATION, "city": "Remote", "url": url, "apply_url": url,
        "description": description or "", "posted_at": posted_at, "valid_through": None,
        "salary_min_lpa": None, "salary_max_lpa": None, "salary_text": salary_text, "remote": 1,
        "country": "India", "raw": {"international_remote": True, "allowed": where},
    }


def himalayas(query, max_age_days=10):
    data = get_json("https://himalayas.app/jobs/api/search", params={"q": query, "country": "India", "limit": 50}, timeout=30)
    out = []
    for j in data.get("jobs") or []:
        where = j.get("locationRestrictions") or []
        if not is_relevant_title(j.get("title")) or not open_to_india(where):
            continue
        out.append(_job("himalayas", j.get("guid") or j.get("applicationLink"), j.get("companyName"), j.get("title"),
                        j.get("applicationLink") or j.get("guid"), html_to_text(j.get("description")), parse_date(j.get("pubDate")),
                        where, _salary(j.get("minSalary"), j.get("maxSalary"), j.get("currency"), j.get("salaryPeriod"))))
    return out


def remotive(category, max_age_days=10):
    data = get_json("https://remotive.com/api/remote-jobs", params={"category": category}, timeout=30)
    out = []
    for j in data.get("jobs") or []:
        where = j.get("candidate_required_location") or ""
        if not is_relevant_title(j.get("title")) or not open_to_india(where):
            continue
        out.append(_job("remotive", j.get("id"), j.get("company_name"), j.get("title"), j.get("url"),
                        html_to_text(j.get("description")), parse_date(j.get("publication_date")), where, j.get("salary") or None))
    return out


def jobicy(geo, max_age_days=10):
    data = get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "geo": geo}, timeout=30)
    out = []
    for j in data.get("jobs") or []:
        where = j.get("jobGeo") or ""
        if not is_relevant_title(j.get("jobTitle")) or not open_to_india(where):
            continue
        out.append(_job("jobicy", j.get("id"), j.get("companyName"), j.get("jobTitle"), j.get("url"),
                        html_to_text(j.get("jobDescription")), parse_date(j.get("pubDate")), where,
                        _salary(j.get("annualSalaryMin"), j.get("annualSalaryMax"), j.get("salaryCurrency"))))
    return out


def remoteok(_=None, max_age_days=10):
    data = get_json("https://remoteok.com/api", timeout=30)
    out = []
    for j in data[1:] if isinstance(data, list) else []:
        where = j.get("location") or ""
        if not is_relevant_title(j.get("position")) or not open_to_india(where):
            continue
        out.append(_job("remoteok", j.get("id"), j.get("company"), j.get("position"), j.get("url"),
                        html_to_text(j.get("description")), parse_date(j.get("date")), where,
                        _salary(j.get("salary_min"), j.get("salary_max"), "USD")))
    return out


def tasks(keywords, max_age_days=10):
    """(label, fn) pairs for one scan: a Himalayas search per keyword plus one feed read from each other board."""
    out = [(f"himalayas: {kw}", lambda kw=kw: himalayas(kw, max_age_days)) for kw in keywords]
    out += [(f"remotive: {c}", lambda c=c: remotive(c, max_age_days)) for c in ("marketing", "product", "sales-business")]
    out += [(f"jobicy: {g}", lambda g=g: jobicy(g, max_age_days)) for g in ("apac", "anywhere")]
    out.append(("remoteok: all", lambda: remoteok(None, max_age_days)))
    return out


def is_international(job):
    return job.get("source") in SOURCES
