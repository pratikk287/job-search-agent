"""Employer career sites via their public job-board APIs (the same endpoints their careers pages use)."""
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from ..keywords import is_relevant_title
from .common import (age_days, get_json, html_to_text, is_india, is_remote, parse_date, parse_relative,
                     parse_salary_lpa, post_json, salary_from_numbers, session)


def _job(company, source, ext_id, title, location, url, apply_url, description, posted_at,
         salary=(None, None, None), remote=None, valid_through=None, raw=None, country=None):
    lo, hi, txt = salary
    if lo is None and hi is None:
        lo, hi, txt = parse_salary_lpa(description)
    return {
        "source": source, "source_type": "employer", "publisher": f"{company['name']} careers",
        "external_id": str(ext_id), "company": company["name"], "company_domain": company.get("domain"),
        "title": title.strip(), "location": location or "", "url": url, "apply_url": apply_url or url,
        "description": description or "", "posted_at": posted_at, "valid_through": valid_through,
        "salary_min_lpa": lo, "salary_max_lpa": hi, "salary_text": txt,
        "remote": int(bool(remote if remote is not None else is_remote(location))),
        "country": country, "raw": raw,
    }


def greenhouse(company, keywords=None):
    slug = company["slug"]
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", timeout=60)
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        offices = ", ".join(o.get("name", "") for o in j.get("offices") or [])
        loc_all = f"{loc} {offices}"
        if not is_relevant_title(j["title"]) or not is_india(loc_all):
            continue
        out.append(_job(company, "greenhouse", j["id"], j["title"], loc or offices, j["absolute_url"], j["absolute_url"],
                        html_to_text(j.get("content")), parse_date(j.get("first_published") or j.get("updated_at")),
                        valid_through=parse_date(j.get("application_deadline")),
                        raw={"updated_at": j.get("updated_at"), "first_published": j.get("first_published")}))
    return out


def lever(company, keywords=None):
    slug = company["slug"]
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=60)
    out = []
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location", "")]
        loc = ", ".join(l for l in locs if l)
        if not is_relevant_title(j["text"]) or not is_india(loc, j.get("country")):
            continue
        parts = [j.get("openingPlain") or "", j.get("descriptionPlain") or ""]
        for lst in j.get("lists") or []:
            parts.append(lst.get("text", ""))
            parts.append(html_to_text(lst.get("content", "")))
        parts.append(j.get("additionalPlain") or "")
        sal = j.get("salaryRange") or {}
        lo, hi = salary_from_numbers(sal.get("min"), sal.get("max"), sal.get("currency"), sal.get("interval", "YEAR").replace("per-year-salary", "YEAR"))
        out.append(_job(company, "lever", j["id"], j["text"], loc, j["hostedUrl"], j.get("applyUrl"),
                        "\n".join(p for p in parts if p), parse_date(j.get("createdAt")),
                        salary=(lo, hi, None) if (lo or hi) else (None, None, None),
                        remote=(j.get("workplaceType") == "remote") or None, country=j.get("country")))
    return out


def ashby(company, keywords=None):
    slug = company["slug"]
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", timeout=60)
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
        loc = ", ".join(l for l in locs if l)
        if not is_relevant_title(j["title"]) or not is_india(loc, country):
            continue
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        out.append(_job(company, "ashby", j["id"], j["title"], loc, j["jobUrl"], j.get("applyUrl"),
                        j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                        parse_date(j.get("publishedAt")), salary=parse_salary_lpa(comp),
                        remote=j.get("isRemote"), country=country))
    return out


def smartrecruiters(company, keywords=None):
    slug = company["slug"]
    out, offset, candidates = [], 0, []
    while True:
        data = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?country=in&limit=100&offset={offset}")
        content = data.get("content", [])
        for j in content:
            loc = j.get("location") or {}
            loc_txt = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
            if is_relevant_title(j["name"]) and is_india(loc_txt, loc.get("country")):
                candidates.append((j, loc_txt, loc))
        offset += len(content)
        if not content or offset >= data.get("totalFound", 0) or offset >= 1000:
            break

    def detail(item):
        j, loc_txt, loc = item
        d = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j['id']}")
        sections = (d.get("jobAd") or {}).get("sections") or {}
        desc = "\n\n".join(html_to_text((sections.get(k) or {}).get("text", ""))
                           for k in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"))
        url = d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{slug}/{j['id']}"
        return _job(company, "smartrecruiters", j["id"], j["name"], loc_txt, url, d.get("applyUrl") or url, desc,
                    parse_date(j.get("releasedDate")), remote=loc.get("remote"), country=loc.get("country"))

    with ThreadPoolExecutor(6) as ex:
        out = list(ex.map(detail, candidates))
    return out


WORKDAY_TERMS = ["product marketing", "growth", "product manager", "category", "marketplace",
                 "ecommerce", "go to market", "strategy", "commercial"]


def workday(company, keywords=None, max_age_days=10):
    host, site = company["host"], company["site"]
    tenant = host.split(".")[0]
    base = f"https://{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    seen, candidates = set(), []
    for term in WORKDAY_TERMS:
        data = post_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": f"{term} India"})
        for p in data.get("jobPostings") or []:
            path = p.get("externalPath")
            if not path or path in seen:
                continue
            seen.add(path)
            posted = parse_relative(p.get("postedOn", ""))
            if not is_relevant_title(p.get("title", "")):
                continue
            if posted and age_days(posted) > max_age_days:
                continue
            candidates.append(p)

    def detail(p):
        d = get_json(f"{base}{p['externalPath']}").get("jobPostingInfo") or {}
        locs = [d.get("location") or ""] + list(d.get("additionalLocations") or [])
        loc = ", ".join(l for l in locs if l)
        if not is_india(loc + " " + (d.get("country") or {}).get("descriptor", "")):
            return None
        url = d.get("externalUrl") or f"https://{host}.myworkdayjobs.com/{site}{p['externalPath']}"
        return _job(company, "workday", d.get("jobReqId") or p["externalPath"], d.get("title") or p["title"], loc, url, url,
                    html_to_text(d.get("jobDescription")),
                    parse_date(d.get("startDate")) or parse_relative(p.get("postedOn", "")),
                    valid_through=parse_date(d.get("endDate")), remote=d.get("remoteType") == "Remote" or None)

    with ThreadPoolExecutor(6) as ex:
        return [j for j in ex.map(detail, candidates) if j]


AMAZON_TERMS = ["category manager", "product marketing", "product manager", "marketplace", "seller",
                "growth marketing", "program manager marketing", "vendor manager", "brand specialist"]


def amazon(company, keywords=None):
    seen, out = set(), []
    for term in AMAZON_TERMS:
        data = get_json("https://www.amazon.jobs/en/search.json",
                        params={"base_query": term, "loc_query": "India", "country": "IND",
                                "result_limit": 50, "sort": "recent"})
        for j in data.get("jobs", []):
            if j["id_icims"] in seen or not is_relevant_title(j["title"]):
                continue
            seen.add(j["id_icims"])
            desc = "\n\n".join(html_to_text(j.get(k, "")) for k in ("description", "basic_qualifications", "preferred_qualifications"))
            url = "https://www.amazon.jobs" + j["job_path"]
            out.append(_job(company, "amazon", j["id_icims"], j["title"], j.get("normalized_location") or j.get("location"),
                            url, j.get("url_next_step") or url, desc, parse_date(j.get("posted_date")),
                            country=j.get("country_code")))
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters,
            "workday": workday, "amazon": amazon}


def detect_ats(url):
    """Work out which ATS a careers URL uses (directly, or by looking inside the careers page)."""
    import re
    patterns = [
        ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
        ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"),
        ("lever", r"jobs\.lever\.co/([\w.-]+)"),
        ("ashby", r"jobs\.ashbyhq\.com/([\w.-]+)"),
        ("smartrecruiters", r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)"),
        ("workday", r"([\w-]+\.wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)"),
    ]

    def match(text):
        for ats, rx in patterns:
            m = re.search(rx, text)
            if m:
                if ats == "workday":
                    return {"ats": ats, "host": m.group(1), "site": m.group(2)}
                if m.group(1) not in ("embed", "v1", "jobs"):
                    return {"ats": ats, "slug": m.group(1)}
        return None

    found = match(url)
    if found:
        return found
    r = session.get(url, timeout=20)
    return match(r.text)
