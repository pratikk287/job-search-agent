"""Core behaviour tests. Run:  python -m pytest tests -q   (or  python tests/test_core.py)"""
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobagent import config, resumes, scan, scoring, verify  # noqa: E402
from jobagent.keywords import classify_title, is_relevant_title  # noqa: E402
from jobagent.sources.common import parse_date, parse_relative, parse_salary_lpa, to_iso, utcnow  # noqa: E402


def _job(**kw):
    base = {"source": "test", "source_type": "employer", "external_id": "1", "company": "TestCo", "title": "Product Marketing Manager",
            "location": "Bengaluru, India", "description": "We need GTM, positioning, SQL and funnel analysis. 3-6 years of experience.",
            "posted_at": to_iso(utcnow() - timedelta(days=2)), "url": "", "apply_url": "", "remote": 0}
    base.update(kw)
    return base


def _have_resumes():
    return all((config.ROOT / v["file"]).exists() for v in config.profile()["resumes"].values())


def test_resumes_parse():
    if not _have_resumes():
        return                                   # fresh install: add your resume PDFs to resumes/ to run this check
    for key in ("A", "B"):
        r = resumes.load(key)
        assert r["name"] == config.profile()["name"]
        assert len(r["experience"]) == 4
        assert r["experience"][0]["end"] == "Present"
        assert all(role["bullets"] for role in r["experience"])


def test_resume_selection():
    if not _have_resumes():
        return
    assert resumes.select_resume("marketplace_category", "seller onboarding, pricing, assortment, GMV", "Category Manager")[0] == "B"
    assert resumes.select_resume("product_marketing", "GTM launch positioning funnel", "Product Marketing Manager")[0] == "A"


def test_title_rules():
    assert classify_title("Senior Product Marketing Manager")[0] == "product_marketing"
    assert classify_title("Category Manager, Beauty")[0] == "marketplace_category"
    assert classify_title("AI Product Manager")[0] == "ai_product"
    assert not is_relevant_title("Senior Software Engineer")
    assert not is_relevant_title("Senior Technical Program Manager")
    assert not is_relevant_title("Talent Acquisition Partner")


def test_freshness_limit_is_enforced():
    s = config.settings()
    assert s["max_job_age_days"] <= 10
    assert scan.accept(_job(), s) is None
    assert scan.accept(_job(posted_at=to_iso(utcnow() - timedelta(days=11))), s) == "older than limit"
    assert scan.accept(_job(posted_at=None), s) == "no posting date"
    assert scan.accept(_job(location="London, UK", country="GB"), s) == "outside India"


def test_scoring():
    if not _have_resumes():
        return
    good = scoring.score_job({**_job(), "city": "Bengaluru"})
    assert good["score"] >= 70, good
    senior = scoring.score_job({**_job(title="Director, Product Marketing"), "city": "Bengaluru"})
    assert senior["score"] <= 45
    far = scoring.score_job({**_job(description="Requires 10+ years of experience in marketing."), "city": "Bengaluru"})
    assert far["parts"]["experience"]["points"] == 0
    low_pay = scoring.score_job({**_job(), "city": "Bengaluru", "salary_min_lpa": 8, "salary_max_lpa": 12})
    assert low_pay["parts"]["salary"]["points"] == 0


def test_date_and_salary_parsing():
    assert parse_date("October 12, 2026").startswith("2026-10-12")
    assert parse_date(1790573982021).startswith("2026-")
    assert parse_relative("Posted 30+ Days Ago") is not None
    assert parse_salary_lpa("CTC: 18-24 LPA")[:2] == (18.0, 24.0)
    assert parse_salary_lpa("₹ 20,00,000 - ₹ 26,00,000 per annum")[:2] == (20.0, 26.0)


def test_scam_detection():
    res = verify.check({**_job(source_type="aggregator", description="Pay a registration fee of Rs 2000. " * 20)}, fetch_page=False)
    assert res["status"] == "suspicious"
    ok = verify.check({**_job(description="Own GTM for our product. " * 30)}, fetch_page=False)
    assert ok["status"] == "verified_open"


NAUKRI_SEARCH = """1 - 2 of 2
- ## [Product Marketing Specialist](https://www.naukri.com/job-listings-product-marketing-specialist-aptean-bengaluru-2-to-4-years-221026502042 "x")

[Aptean](https://www.naukri.com/aptean-jobs-careers-84674 "Aptean") [3.0](https://www.ambitionbox.com/reviews/aptean-reviews "r")

2-4 YrsBengaluru

  - B2B

2 days agosave

- ## [Sr. Manager Product Marketing - B2B SaaS](https://www.naukri.com/job-listings-sr-manager-product-marketing-b2b-saas-mahasurya-ventures-india-noida-hyderabad-bengaluru-15-to-24-years-241026002406 "x")

Global IT & Software Company

[Posted by Mahasurya Ventures India](https://www.naukri.com/mahasurya-ventures-india-jobs-careers-6221060 "p")

15-24 YrsHybrid - Bengaluru, Noida, Hyderabad

1 day agosave
"""

NAUKRI_DETAIL = """[Aptean](https://www.naukri.com/aptean-jobs-careers-84674)

2 - 4 years

Not Disclosed

Posted: 1 day agoOpenings: 1Applicants: 19

## Job description

We are looking for a Product Marketing Specialist to execute go-to-market activities and positioning.
Qualifications: 2-4 years of experience in product marketing.

Role: [Product Marketing](https://www.naukri.com/product-marketing-jobs),
"""

INDEED_SEARCH = """- |     |
| --- |
| ### [Product Marketing Consultant](https://in.indeed.com/rc/clk?jk=7c561a60aca37937&bb=x&vjs=3)<br>Zycus<br>Bengaluru, Karnataka |
| ### [Business Intelligence Analyst IV](https://in.indeed.com/rc/clk?jk=693a82236f378fb3&vjs=3)<br>![](logo.png)<br>Applied Materials<br>Bengaluru, Karnataka |
"""


def test_firecrawl_parsers():
    from jobagent.sources import firecrawl as fc
    cards = fc.parse_naukri_search(NAUKRI_SEARCH)
    assert [c["title"] for c in cards] == ["Product Marketing Specialist", "Sr. Manager Product Marketing - B2B SaaS"]
    assert cards[0]["company"] == "Aptean" and cards[0]["posted"] == "2 days ago" and cards[0]["external_id"] == "221026502042"
    assert cards[1]["company"] == "Mahasurya Ventures India" and cards[1]["posted"] == "1 day ago"
    d = fc.parse_naukri_detail(NAUKRI_DETAIL)
    assert d["posted"] == "1 day ago" and d["salary"] == "Not Disclosed" and "go-to-market" in d["description"]
    ind = fc.parse_indeed_search(INDEED_SEARCH)
    assert ind[0] == {"title": "Product Marketing Consultant", "jk": "7c561a60aca37937", "company": "Zycus", "location": "Bengaluru, Karnataka"}
    assert ind[1]["company"] == "Applied Materials"


def test_naukri_source_uses_budget():
    from jobagent.sources import firecrawl as fc
    calls = []

    def fake(url):
        calls.append(url)
        return NAUKRI_SEARCH if "jobAge" in url else NAUKRI_DETAIL
    real, fc.scrape = fc.scrape, fake
    try:
        fc._budget["naukri"] = 1
        jobs = fc.naukri("product marketing manager", "Bengaluru")
    finally:
        fc.scrape = real
    assert calls[0] == "https://www.naukri.com/product-marketing-manager-jobs-in-bengaluru?jobAge=7"
    assert len(jobs) <= 1 and len(calls) == 1 + len(jobs)      # never more detail pages than the budget
    for j in jobs:
        assert j["posted_at"] and j["company"] and j["source"] == "naukri"


def test_company_site_priority():
    from jobagent import scan
    portal = {"score": 90, "source_type": "aggregator"}
    employer = {"score": 85, "source_type": "employer"}
    assert scan.priority(employer) > scan.priority(portal)       # a company-site job outranks a slightly better portal one
    assert scan.priority({"score": 70, "source_type": "employer"}) < scan.priority(portal)


def test_apollo_flow_reveals_only_top_people():
    from jobagent import contacts, config
    calls = []

    def fake(path, body=None, params=None):
        calls.append(path)
        if path.endswith("api_search"):
            return {"people": [
                {"id": "r1", "title": "Talent Acquisition Partner", "has_email": True, "organization": {"name": "Emeritus"}},
                {"id": "m1", "title": "Head of Product Marketing", "has_email": True, "organization": {"name": "Emeritus"}},
                {"id": "x1", "title": "Software Engineer", "has_email": True, "organization": {"name": "Emeritus"}},
                {"id": "w1", "title": "Recruiter", "has_email": True, "organization": {"name": "Other Corp"}},
                {"id": "n1", "title": "Recruiter", "has_email": False, "organization": {"name": "Emeritus"}}]}
        return {"person": {"name": "Priya Sharma", "title": "Talent Acquisition Partner", "email": "priya@emeritus.org",
                           "email_status": "verified", "linkedin_url": "https://linkedin.com/in/priya"}}
    real_post, real_key = contacts._apollo, config.secret
    contacts._apollo = fake
    config.secret = lambda k: "key" if k == "APOLLO_API_KEY" else real_key(k)
    try:
        out, note = contacts.apollo({"company": "Emeritus", "function": "product_marketing"}, None, max_reveals=3)
    finally:
        contacts._apollo, config.secret = real_post, real_key
    assert calls.count("/people/match") == 2                      # only the 2 relevant people were revealed (credits)
    assert out[0]["role_type"] == "recruiter" and out[0]["email_status"] == "verified"


def test_search_mix_rule():
    from jobagent import mix
    snap = {"firecrawl": {"remaining": 630, "limit": 1000, "fraction": .63}, "jsearch": {"remaining": 135, "limit": 200, "fraction": .67},
            "apify": {"remaining": 5.0, "limit": 5.0, "fraction": 1.0}, "jooble": {"remaining": 500, "limit": 500, "fraction": 1.0}}
    integ = {"apify": True, "naukri": True, "indeed": True, "jsearch": True, "adzuna": True, "jooble": True}
    s = {"company_share": 0.5, "auto_balance": True, "apify_max_usd_per_scan": 0.6, "apify_results_per_query": 25}
    c = mix.effective_share(s, snap)
    plan = mix.allocate(46, c, s, snap, integ)
    assert c == 0.5 and 40 <= sum(plan.values()) <= 48                    # portals get about as many searches as company sites
    assert plan["apify"] * 0.0501 <= 0.6                                  # LinkedIn spend stays inside its per-scan dollar cap
    low = {**snap, "jsearch": {"remaining": 30, "limit": 200, "fraction": .15}}
    assert mix.effective_share(s, low) >= 0.7                             # a low pool raises the company share automatically
    assert mix.group_of_label("Amazon (amazon)") == "company" and mix.group_of_label("apify: x / y") == "linkedin"


def test_apify_item_mapping():
    from jobagent.sources import apify
    j = apify.map_item({"id": "1", "link": "https://www.linkedin.com/jobs/view/a-1?trk=x", "title": "Category Manager", "companyName": "Acme",
                        "location": "Pune, Maharashtra, India", "postedAt": "2026-10-23", "salaryInfo": ["₹1,500,000", "₹2,000,000"], "descriptionHtml": "<p>Own pricing</p>"})
    assert j["url"] == "https://www.linkedin.com/jobs/view/a-1" and j["publisher"] == "LinkedIn" and j["salary_min_lpa"] == 15.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
