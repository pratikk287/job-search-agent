"""Naukri and Indeed through Firecrawl (opt-in, low volume).

The candidate chose to enable this knowing it goes against both portals' terms of use. To keep it light:
  * a small, rotating number of search pages per scan (settings: naukri/indeed_max_searches_per_scan)
  * a cap on job-detail pages per portal per scan (settings: naukri/indeed_max_detail_pages_per_scan)
  * detail pages are fetched only for relevant, fresh titles that are not already stored
No login is used, so the candidate's portal accounts are never involved.
"""
import re
import threading
import time
from datetime import timedelta
from urllib.parse import quote_plus

import requests

from .. import config, db
from ..keywords import is_relevant_title
from .common import age_days, cities_in, parse_relative, parse_salary_lpa, to_iso, utcnow

API = "https://api.firecrawl.dev/v2/scrape"
_budget = {"naukri": 0, "indeed": 0}
_lock = threading.Lock()
_call_lock = threading.Lock()
_last_call = [0.0]


class FirecrawlError(RuntimeError):
    pass


def reset_budget():
    with _lock:
        s = config.settings()
        for portal, default in (("naukri", 12), ("indeed", 8)):
            _budget[portal] = s.get(f"{portal}_max_detail_pages_per_scan", default)


def _take_detail_credit(portal):
    with _lock:
        if _budget[portal] <= 0:
            return False
        _budget[portal] -= 1
        return True


def scrape(url):
    """Markdown of one page (1 Firecrawl credit)."""
    key = config.secret("FIRECRAWL_API_KEY")
    if not key:
        raise FirecrawlError("FIRECRAWL_API_KEY is not set")
    with _call_lock:      # one call at a time, spaced out: the free plan has a per-minute limit
        gap = config.settings().get("firecrawl_min_seconds_between_calls", 6)
        wait = _last_call[0] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            r = requests.post(API, json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
                              headers={"Authorization": f"Bearer {key}"}, timeout=90)
            if r.status_code != 429:
                break
            time.sleep(20 * (attempt + 1))
        _last_call[0] = time.time()
    if r.status_code == 402:
        raise FirecrawlError("Firecrawl credits exhausted")
    if r.status_code == 429:
        raise FirecrawlError("Firecrawl rate limit hit")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok or not body.get("success", True):
        raise FirecrawlError(f"Firecrawl HTTP {r.status_code}: {str(body.get('error') or r.text)[:150]}")
    return (body.get("data") or {}).get("markdown") or ""


def search(query, limit=8):
    """Public web search results [{url, title, description}] (about 2 Firecrawl credits)."""
    key = config.secret("FIRECRAWL_API_KEY")
    if not key:
        raise FirecrawlError("FIRECRAWL_API_KEY is not set")
    with _call_lock:
        gap = config.settings().get("firecrawl_min_seconds_between_calls", 6)
        wait = _last_call[0] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            r = requests.post("https://api.firecrawl.dev/v2/search", json={"query": query, "limit": limit, "sources": ["web"]},
                              headers={"Authorization": f"Bearer {key}"}, timeout=90)
            if r.status_code != 429:
                break
            time.sleep(20 * (attempt + 1))
        _last_call[0] = time.time()
    if r.status_code == 402:
        raise FirecrawlError("Firecrawl credits exhausted")
    if not r.ok:
        raise FirecrawlError(f"Firecrawl search HTTP {r.status_code}")
    data = r.json().get("data")
    return (data.get("web") if isinstance(data, dict) else data) or []


def _known(source, ext_id):
    return db.one("SELECT 1 FROM jobs WHERE source = ? AND external_id = ?", (source, ext_id)) is not None


def _job(source, publisher, ext_id, title, company, location, url, description, posted_at, salary_text=None, raw=None):
    lo, hi, txt = parse_salary_lpa(salary_text or "")
    if lo is None and hi is None:
        lo, hi, txt = parse_salary_lpa(description)
    return {"source": source, "source_type": "aggregator", "publisher": publisher, "external_id": ext_id,
            "company": company or "Unknown", "company_domain": None, "title": title, "location": location,
            "url": url, "apply_url": url, "description": description, "posted_at": posted_at, "valid_through": None,
            "salary_min_lpa": lo, "salary_max_lpa": hi, "salary_text": txt, "remote": int("remote" in (location or "").lower()),
            "country": "IN", "raw": raw}


# ------------------------------------------------------------------ Naukri

NAUKRI_CARD = re.compile(r"##\s*\[(?P<title>[^\]]+)\]\((?P<url>https://www\.naukri\.com/job-listings-[^)\s]+)")


def parse_naukri_search(md):
    """Cards from a Naukri search page: title, url, company, meta line, relative date."""
    cards = []
    matches = list(NAUKRI_CARD.finditer(md))
    for i, m in enumerate(matches):
        block = md[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(md)]
        company = None
        for cm in re.finditer(r"\[(?:Posted by )?([^\]]+)\]\((https://www\.naukri\.com/[^)]+jobs-careers[^)]*)\)", block):
            company = cm.group(1).strip()
            break
        if not company:
            plain = [l.strip() for l in block.splitlines() if l.strip() and not l.strip().startswith(("!", "["))]
            company = plain[0] if plain else None
        meta = next((l.strip() for l in block.splitlines() if re.search(r"\bYrs\b", l)), "")
        when = re.search(r"(just now|few hours ago|today|\d+\+?\s*(?:day|days|hour|hours)\s*ago)", block, re.I)
        ext = re.search(r"-(\d{9,})(?:\?|$)", m.group("url"))
        cards.append({"title": m.group("title").strip(), "url": m.group("url"), "company": company, "meta": meta,
                      "posted": when.group(1) if when else None, "external_id": ext.group(1) if ext else m.group("url")})
    return cards


def parse_naukri_detail(md):
    posted = re.search(r"Posted:\s*([^\n]+?)(?:Openings|Applicants|\n)", md)
    desc = re.search(r"##\s*Job description\s*(.+?)(?:\nRole:|\nKey Skills|\n## Jobs you might|\Z)", md, re.S)
    salary = re.search(r"\n(\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*Lacs? P\.?A\.?|Not Disclosed)\n", md, re.I)
    return {"posted": posted.group(1).strip() if posted else None,
            "description": desc.group(1).strip() if desc else md[:6000],
            "salary": salary.group(1) if salary else None,
            "consultant": bool(re.search(r"Posted by [^\n]*(consult|ventures|placement|staffing|hr solutions)", md, re.I))}


def _relative(text):
    t = (text or "").lower()
    if "few hours" in t:
        return to_iso(utcnow())
    return parse_relative(t)


def naukri(query, city, max_age_days=10):
    days = min(max_age_days, 7)
    url = f"https://www.naukri.com/{re.sub(r'[^a-z0-9]+', '-', query.lower()).strip('-')}-jobs-in-{city.lower().replace(' ', '-')}?jobAge={days}"
    out = []
    for c in parse_naukri_search(scrape(url)):
        posted = _relative(c["posted"])
        if not is_relevant_title(c["title"]) or (posted and age_days(posted) > max_age_days) or _known("naukri", c["external_id"]):
            continue
        if not _take_detail_credit("naukri"):
            break
        try:
            d = parse_naukri_detail(scrape(c["url"]))
        except FirecrawlError:
            continue
        posted = _relative(d["posted"]) or posted
        location = ", ".join(cities_in(c["meta"])) or city
        out.append(_job("naukri", "Naukri" + (" (consultant)" if d["consultant"] else ""), c["external_id"], c["title"],
                        c["company"], f"{location}, India", c["url"], d["description"], posted,
                        salary_text=d["salary"] or c["meta"], raw={"listing": c["meta"]}))
    return out


# ------------------------------------------------------------------ Indeed

INDEED_CARD = re.compile(r"###\s*\[(?P<title>[^\]]+)\]\((?P<url>https://in\.indeed\.com/(?:rc/clk|viewjob|pagead/clk)[^)\s]*)\)(?P<rest>[^\n]*)")


def parse_indeed_search(md):
    cards = []
    for m in INDEED_CARD.finditer(md):
        jk = re.search(r"jk=([0-9a-f]{16})", m.group("url"))
        if not jk:
            continue
        parts = [p.strip(" |") for p in m.group("rest").split("<br>") if p.strip(" |") and not p.strip().startswith("![")]
        company, location = (parts + [None, None])[:2]
        cards.append({"title": m.group("title").strip(), "jk": jk.group(1), "company": company, "location": location or ""})
    seen, out = set(), []
    for c in cards:
        if c["jk"] not in seen:
            seen.add(c["jk"])
            out.append(c)
    return out


def parse_indeed_detail(md):
    desc = re.search(r"#### Full job description\s*(.+?)(?:\n### Company and salary|\nIs there a problem|\Z)", md, re.S)
    pay = re.search(r"₹\s?[\d,]+(?:\s*-\s*₹\s?[\d,]+)?\s*(?:a|per)\s*(?:year|month)", md)
    return {"description": (desc.group(1) if desc else md[:6000]).strip(), "pay": pay.group(0) if pay else None}


INDEED_DAYS = 3   # Indeed pages carry no exact date; the search filter gives an upper bound


def indeed(query, city, max_age_days=10):
    days = min(INDEED_DAYS, max_age_days)
    url = f"https://in.indeed.com/jobs?q={quote_plus(query)}&l={quote_plus(city)}&fromage={days}&sort=date"
    posted = to_iso(utcnow() - timedelta(days=days))   # conservative: treat as the oldest the filter allows
    out = []
    for c in parse_indeed_search(scrape(url)):
        if not is_relevant_title(c["title"]) or _known("indeed", c["jk"]):
            continue
        if not _take_detail_credit("indeed"):
            break
        view = f"https://in.indeed.com/viewjob?jk={c['jk']}"
        try:
            d = parse_indeed_detail(scrape(view))
        except FirecrawlError:
            continue
        pay_lpa = None
        if d["pay"]:
            nums = [int(x.replace(",", "")) for x in re.findall(r"[\d,]{4,}", d["pay"])]
            mult = 12 if "month" in d["pay"] else 1
            pay_lpa = "-".join(str(round(n * mult / 1e5, 1)) for n in nums) + " LPA"
        out.append(_job("indeed", "Indeed", c["jk"], c["title"], c["company"], f"{c['location']}, India", view,
                        d["description"], posted, salary_text=pay_lpa,
                        raw={"date_note": f"Indeed shows no exact date; listed under 'last {days} days'"}))
    return out
