"""Job portals through aggregator APIs that are licensed to redistribute listings.

JSearch indexes Google for Jobs, which carries LinkedIn, Naukri, Indeed, Glassdoor, Foundit, Instahyre and
company career pages. Adzuna India is a second, independent index. Neither scrapes portals on our behalf;
both need a free API key (see .env.example).
"""
from urllib.parse import quote_plus, urlparse

from .. import config
from ..keywords import is_relevant_title
from .common import get_json, is_india, is_remote, parse_date, parse_salary_lpa, salary_from_numbers, session


def _job(source, ext_id, company, title, location, url, apply_url, description, posted_at, publisher,
         salary=(None, None, None), remote=None, domain=None, raw=None, country=None):
    lo, hi, txt = salary
    if lo is None and hi is None:
        lo, hi, txt = parse_salary_lpa(description)
    return {
        "source": source, "source_type": "aggregator", "publisher": publisher, "external_id": str(ext_id),
        "company": (company or "").strip() or "Unknown", "company_domain": domain, "title": (title or "").strip(),
        "location": location or "", "url": url, "apply_url": apply_url or url, "description": description or "",
        "posted_at": posted_at, "valid_through": None, "salary_min_lpa": lo, "salary_max_lpa": hi,
        "salary_text": txt, "remote": int(bool(remote if remote is not None else is_remote(location))),
        "country": country, "raw": raw,
    }


def _domain(url):
    if not url:
        return None
    host = urlparse(url if "//" in url else "https://" + url).netloc.lower()
    return host[4:] if host.startswith("www.") else host or None


def jsearch(query, city, max_age_days=10):
    key = config.secret("RAPIDAPI_KEY")
    if not key:
        return []
    date_posted = "week" if max_age_days <= 7 else "month"   # JSearch buckets: today, 3days, week, month
    resp = session.get("https://jsearch.p.rapidapi.com/search-v2",
                    params={"query": f"{query} in {city}, India", "page": 1, "num_pages": 1,   # each page uses quota
                            "country": "in", "date_posted": date_posted},
                    headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}, timeout=45)
    resp.raise_for_status()
    record_jsearch_quota(resp.headers)
    data = resp.json()
    out = []
    payload = data.get("data") or []
    for j in (payload.get("jobs") or []) if isinstance(payload, dict) else payload:   # v2 nests jobs
        loc = ", ".join(x for x in (j.get("job_city"), j.get("job_state"), j.get("job_country")) if x)
        if not is_relevant_title(j.get("job_title")) or not is_india(loc, j.get("job_country")):
            continue
        options = j.get("apply_options") or []
        direct = next((o["apply_link"] for o in options if o.get("is_direct")), None)
        lo, hi = salary_from_numbers(j.get("job_min_salary"), j.get("job_max_salary"),
                                     j.get("job_salary_currency") or "INR", j.get("job_salary_period") or "YEAR")
        out.append(_job("jsearch", j["job_id"], j.get("employer_name"), j.get("job_title"), loc,
                        j.get("job_google_link") or j.get("job_apply_link"), direct or j.get("job_apply_link"),
                        j.get("job_description"),
                        parse_date(j.get("job_posted_at_timestamp")) or parse_date(j.get("job_posted_at_datetime_utc")),
                        j.get("job_publisher") or "Google for Jobs",
                        salary=(lo, hi, None) if (lo or hi) else (None, None, None),
                        remote=j.get("job_is_remote"), domain=_domain(j.get("employer_website")),
                        country=j.get("job_country"),
                        raw={"apply_options": [{"publisher": o.get("publisher"), "link": o.get("apply_link"),
                                                "direct": o.get("is_direct")} for o in options]}))
    return out


def adzuna(query, city, max_age_days=10):
    app_id, app_key = config.secret("ADZUNA_APP_ID"), config.secret("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        return []
    data = get_json("https://api.adzuna.com/v1/api/jobs/in/search/1",
                    params={"app_id": app_id, "app_key": app_key, "what": query, "where": city,
                            "max_days_old": max_age_days, "results_per_page": 50, "sort_by": "date",
                            "content-type": "application/json"}, timeout=30)
    out = []
    for j in data.get("results") or []:
        loc = (j.get("location") or {}).get("display_name", "")
        if not is_relevant_title(j.get("title")) or not is_india(loc + ", India"):
            continue
        predicted = str(j.get("salary_is_predicted")) == "1"
        lo, hi = (None, None) if predicted else salary_from_numbers(j.get("salary_min"), j.get("salary_max"))
        out.append(_job("adzuna", j["id"], (j.get("company") or {}).get("display_name"), j.get("title"), loc,
                        j.get("redirect_url"), j.get("redirect_url"), j.get("description"),
                        parse_date(j.get("created")), "Adzuna",
                        salary=(lo, hi, None) if (lo or hi) else (None, None, None),
                        raw={"description_truncated": True}))
    return out


def record_jsearch_quota(headers):
    """Keep the quota RapidAPI reports in its response headers, so the app knows what is left without spending a search."""
    import json
    try:
        lim, rem = headers.get("x-ratelimit-requests-limit"), headers.get("x-ratelimit-requests-remaining")
        if lim and rem:
            (config.DATA_DIR / "jsearch_quota.json").write_text(json.dumps({"limit": int(lim), "remaining": int(rem)}))
    except Exception:
        pass


JOOBLE_LIFETIME = 500


def jooble_used():
    import json
    try:
        return int(json.loads((config.DATA_DIR / "jooble_used.json").read_text())["used"])
    except Exception:
        return 0


def jooble(query, city, max_age_days=10):
    """Jooble's free key allows 500 requests in total (not per month), so every call is counted."""
    import json
    key = config.secret("JOOBLE_API_KEY")
    if not key or jooble_used() >= JOOBLE_LIFETIME:
        return []
    r = session.post(f"https://jooble.org/api/{key}", json={"keywords": query, "location": f"{city}, India", "page": 1}, timeout=30)
    (config.DATA_DIR / "jooble_used.json").write_text(json.dumps({"used": jooble_used() + 1}))
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs") or []:
        loc = j.get("location") or ""
        if not is_relevant_title(j.get("title")) or not is_india(loc + ", India"):
            continue
        link = j.get("link") or ""
        out.append(_job("jooble", j.get("id") or link, j.get("company"), j.get("title"), loc, link, link,
                        j.get("snippet"), parse_date((j.get("updated") or "")[:19]), j.get("source") or "Jooble",
                        salary=parse_salary_lpa(j.get("salary") or ""), raw={"description_truncated": True}))
    return out


from . import apify, firecrawl  # noqa: E402

AGGREGATORS = {"jsearch": jsearch, "adzuna": adzuna, "jooble": jooble, "apify": apify.linkedin,
               "naukri": firecrawl.naukri, "indeed": firecrawl.indeed}


def portal_links(keyword, city, days=7):
    """Pre-filtered search links for portals that must be browsed by the user (no scraping)."""
    q, c = quote_plus(keyword), quote_plus(city)
    slug_k = keyword.lower().replace(" ", "-")
    slug_c = city.lower().replace(" ", "-")
    secs = days * 86400
    return [
        {"portal": "LinkedIn", "url": f"https://www.linkedin.com/jobs/search/?keywords={q}&location={c}%2C%20India&f_TPR=r{secs}&sortBy=DD"},
        {"portal": "Naukri", "url": f"https://www.naukri.com/{slug_k}-jobs-in-{slug_c}?jobAge={min(days, 15)}"},
        {"portal": "Indeed", "url": f"https://in.indeed.com/jobs?q={q}&l={c}&fromage={min(days, 14)}&sort=date"},
        {"portal": "Foundit", "url": f"https://www.foundit.in/srp/results?query={q}&locations={c}&jobFreshness={days}"},
        {"portal": "Glassdoor", "url": f"https://www.glassdoor.co.in/Job/jobs.htm?sc.keyword={q}&locKeyword={c}&fromAge={days}"},
        {"portal": "iimjobs", "url": f"https://www.iimjobs.com/search/{slug_k}-in-{slug_c}"},
        {"portal": "Instahyre", "url": f"https://www.instahyre.com/search-jobs/?q={q}&l={c}"},
        {"portal": "Cutshort", "url": f"https://cutshort.io/jobs?q={q}&location={c}"},
        {"portal": "Michael Page", "url": f"https://www.michaelpage.co.in/jobs/{slug_k}"},
        {"portal": "Wellfound", "url": f"https://wellfound.com/jobs?keywords={q}&locations={c}"},
        {"portal": "Google Jobs", "url": f"https://www.google.com/search?q={q}+jobs+in+{c}&ibp=htl;jobs&htichips=date_posted:week"},
    ]
