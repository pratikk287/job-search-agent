"""LinkedIn jobs through Apify (actor: curious_coder/linkedin-jobs-scraper).

Free Apify plan = $5 of usage a month, no rollover. This actor charges about $0.002 per job on the free tier, so every
run is capped three ways: results per search (apify_results_per_query), a dollar cap on the run itself
(maxTotalChargeUsd), and a per-scan and monthly budget enforced by jobagent/mix.py before any run starts.
Apify scrapes LinkedIn's public job pages; that is against LinkedIn's terms, the candidate accepted this risk.
"""
import requests

from .. import config
from .common import html_to_text, parse_date

ACTOR = "curious_coder~linkedin-jobs-scraper"
PRICE_PER_RESULT = 0.002          # free-plan rate (no volume discount)


class ApifyError(RuntimeError):
    pass


def results_per_query():
    return int(config.settings().get("apify_results_per_query", 25))


def query_cost_usd():
    return results_per_query() * PRICE_PER_RESULT + 0.0001


def usage():
    """{'used': usd this month, 'limit': usd allowed} from Apify, or None."""
    token = config.secret("APIFY_TOKEN")
    if not token:
        return None
    try:
        r = requests.get("https://api.apify.com/v2/users/me/limits", params={"token": token}, timeout=20)
        r.raise_for_status()
        d = r.json()["data"]
        return {"used": float(d["current"]["monthlyUsageUsd"]), "limit": float(d["limits"]["maxMonthlyUsageUsd"])}
    except Exception:
        return None


def _salary_lpa(info):
    """salaryInfo is a list of strings like ['₹1,200,000', '₹1,800,000']; only INR is converted."""
    nums = []
    for s in info or []:
        if "₹" not in str(s) and "INR" not in str(s).upper():
            return None, None
        digits = "".join(ch for ch in str(s) if ch.isdigit())
        if digits:
            nums.append(int(digits))
    if not nums:
        return None, None
    lo, hi = min(nums), max(nums)
    return round(lo / 1e5, 1), round(hi / 1e5, 1)


def map_item(it):
    link = (it.get("link") or "").split("?")[0]
    lo, hi = _salary_lpa(it.get("salaryInfo"))
    desc = it.get("descriptionText") or html_to_text(it.get("descriptionHtml") or "")
    return {
        "source": "apify", "source_type": "aggregator", "publisher": "LinkedIn", "external_id": str(it.get("id") or link),
        "company": it.get("companyName") or "Unknown", "company_domain": None, "title": (it.get("title") or "").strip(),
        "location": it.get("location") or "", "url": link, "apply_url": it.get("applyUrl") or link, "description": desc,
        "posted_at": parse_date(it.get("postedAt")), "valid_through": None, "salary_min_lpa": lo, "salary_max_lpa": hi,
        "salary_text": None, "remote": int("remote" in (it.get("location") or "").lower()), "country": "IN", "raw": None,
    }


def linkedin(query, city, max_age_days=10):
    token = config.secret("APIFY_TOKEN")
    if not token:
        return []
    limit = results_per_query()
    body = {"keywords": query, "location": f"{city}, India", "datePosted": "past24Hours" if max_age_days <= 1 else "pastWeek",
            "limitPerSource": limit, "scrapeCompany": False}
    r = requests.post(f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items",
                      params={"token": token, "timeout": 150, "memory": 1024, "maxItems": limit,
                              "maxTotalChargeUsd": round(limit * PRICE_PER_RESULT * 1.5 + 0.01, 3)},
                      json=body, timeout=180)
    if r.status_code in (401, 403):
        raise ApifyError("Apify rejected the token")
    if r.status_code == 402:
        raise ApifyError("Apify monthly usage is used up")
    if not r.ok:
        raise ApifyError(f"Apify HTTP {r.status_code}: {r.text[:120]}")
    items = r.json()
    from ..keywords import is_relevant_title
    from .common import is_india
    out = []
    for it in items if isinstance(items, list) else []:
        job = map_item(it)
        if job["title"] and is_relevant_title(job["title"]) and is_india(job["location"], "IN"):
            out.append(job)
    return out
