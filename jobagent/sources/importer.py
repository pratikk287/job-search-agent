"""Import a single job from a URL the user pastes (any portal or careers page), or from pasted text."""
import hashlib
import json
import re

from lxml import html as lxml_html

from .common import html_to_text, is_india, is_remote, parse_date, parse_salary_lpa, session
from .employers import detect_ats


def _jsonld_jobs(doc):
    out = []
    for node in doc.xpath('//script[@type="application/ld+json"]'):
        try:
            data = json.loads(node.text_content())
        except (ValueError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if item.get("@type") in ("JobPosting", ["JobPosting"]):
                    out.append(item)
                stack.extend(v for k, v in item.items() if k == "@graph" and isinstance(v, list))
    return out


def _loc_text(posting):
    locs = posting.get("jobLocation") or []
    if isinstance(locs, dict):
        locs = [locs]
    parts = []
    for l in locs:
        addr = (l or {}).get("address") or {}
        if isinstance(addr, str):
            parts.append(addr)
        else:
            parts.append(", ".join(str(addr.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")
                                   if addr.get(k) and not isinstance(addr.get(k), dict)))
    if posting.get("jobLocationType") == "TELECOMMUTE":
        parts.append("Remote")
    return "; ".join(p for p in parts if p)


def _ats_single(url, ats):
    """Exact data straight from the employer ATS for known job URLs."""
    if ats["ats"] == "greenhouse":
        m = re.search(r"/jobs/(\d+)", url) or re.search(r"gh_jid=(\d+)", url)
        if m:
            j = session.get(f"https://boards-api.greenhouse.io/v1/boards/{ats['slug']}/jobs/{m.group(1)}", timeout=20).json()
            return {"source": "greenhouse", "source_type": "employer", "external_id": str(j["id"]), "title": j["title"],
                    "company": j.get("company_name") or ats["slug"], "location": (j.get("location") or {}).get("name", ""),
                    "description": html_to_text(j.get("content")), "url": j["absolute_url"], "apply_url": j["absolute_url"],
                    "posted_at": parse_date(j.get("first_published") or j.get("updated_at"))}
    if ats["ats"] == "lever":
        m = re.search(r"jobs\.lever\.co/[\w.-]+/([0-9a-f-]{36})", url)
        if m:
            j = session.get(f"https://api.lever.co/v0/postings/{ats['slug']}/{m.group(1)}", timeout=20).json()
            cats = j.get("categories") or {}
            desc = "\n".join([j.get("descriptionPlain") or ""] + [html_to_text(l.get("content", "")) for l in j.get("lists") or []])
            return {"source": "lever", "source_type": "employer", "external_id": j["id"], "title": j["text"],
                    "company": ats["slug"], "location": cats.get("location", ""), "description": desc,
                    "url": j["hostedUrl"], "apply_url": j.get("applyUrl"), "posted_at": parse_date(j.get("createdAt")),
                    "country": j.get("country")}
    if ats["ats"] == "ashby":
        m = re.search(r"jobs\.ashbyhq\.com/[\w.-]+/([0-9a-f-]{36})", url)
        if m:
            data = session.get(f"https://api.ashbyhq.com/posting-api/job-board/{ats['slug']}", timeout=20).json()
            for j in data.get("jobs", []):
                if j["id"] == m.group(1):
                    return {"source": "ashby", "source_type": "employer", "external_id": j["id"], "title": j["title"],
                            "company": ats["slug"], "location": j.get("location", ""),
                            "description": j.get("descriptionPlain", ""), "url": j["jobUrl"], "apply_url": j.get("applyUrl"),
                            "posted_at": parse_date(j.get("publishedAt"))}
    return None


def from_url(url):
    """Best-effort structured import. Returns (job_dict, problems)."""
    problems = []
    try:
        ats = detect_ats(url) if re.search(r"greenhouse|lever\.co|ashbyhq", url) else None
        if ats:
            job = _ats_single(url, ats)
            if job:
                job.setdefault("publisher", f"{job['company']} careers")
                return _finish(job), problems
    except Exception as e:  # fall back to page parsing
        problems.append(f"ATS lookup failed: {e}")
    r = session.get(url, timeout=25, headers={"Accept": "text/html"})
    if r.status_code >= 400:
        return None, [f"Page returned HTTP {r.status_code}. The portal may block automated access; paste the job text instead."]
    doc = lxml_html.fromstring(r.content)
    postings = _jsonld_jobs(doc)
    host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url).split("/")[0])
    if postings:
        p = postings[0]
        org = p.get("hiringOrganization") or {}
        org = org if isinstance(org, dict) else {"name": str(org)}
        sal = p.get("baseSalary") or {}
        lo = hi = None
        if isinstance(sal, dict):
            val = sal.get("value") or {}
            if isinstance(val, dict) and str(sal.get("currency", "INR")).upper() == "INR":
                from .common import salary_from_numbers
                lo, hi = salary_from_numbers(val.get("minValue") or val.get("value"), val.get("maxValue"),
                                             "INR", val.get("unitText") or "YEAR")
        job = {
            "source": "import", "source_type": "employer" if org.get("sameAs") and host in str(org.get("sameAs")) else "manual",
            "publisher": host, "external_id": str((p.get("identifier") or {}).get("value") if isinstance(p.get("identifier"), dict) else p.get("identifier") or ""),
            "title": html_to_text(p.get("title", "")), "company": org.get("name", ""),
            "company_domain": re.sub(r"^https?://(www\.)?", "", str(org.get("sameAs") or "")).split("/")[0] or None,
            "location": _loc_text(p), "description": html_to_text(p.get("description", "")),
            "url": url, "apply_url": url, "posted_at": parse_date(p.get("datePosted")),
            "valid_through": parse_date(p.get("validThrough")), "salary_min_lpa": lo, "salary_max_lpa": hi,
        }
        return _finish(job), problems
    title = (doc.findtext(".//title") or "").strip()
    problems.append("No structured job data on the page; review the title, company and posting date before saving.")
    body = html_to_text(lxml_html.tostring(doc.body or doc, encoding="unicode"))[:15000]
    return _finish({"source": "import", "source_type": "manual", "publisher": host, "title": title, "company": "",
                    "location": "", "description": body, "url": url, "apply_url": url, "posted_at": None}), problems


def from_text(title, company, location, description, url="", posted_at=None, publisher="manual"):
    return _finish({"source": "manual", "source_type": "manual", "publisher": publisher, "title": title,
                    "company": company, "location": location, "description": description, "url": url,
                    "apply_url": url, "posted_at": parse_date(posted_at)})


def _finish(job):
    job.setdefault("external_id", "")
    if not job.get("external_id"):
        job["external_id"] = hashlib.sha1((job.get("url") or job.get("title", "") + job.get("company", "")).encode()).hexdigest()[:16]
    if job.get("salary_min_lpa") is None and job.get("salary_max_lpa") is None:
        lo, hi, txt = parse_salary_lpa(job.get("description"))
        job.update({"salary_min_lpa": lo, "salary_max_lpa": hi, "salary_text": txt})
    job.setdefault("remote", int(is_remote(job.get("location"))))
    job.setdefault("country", "IN" if is_india(job.get("location")) else None)
    return job
