"""Search mix and credit budgeting.

Rule: a share of all searches (default 50%) goes to company career sites, which are free and put you straight on the
employer's own form. The rest is split between LinkedIn (Apify), Naukri/Indeed (Firecrawl) and other portals (JSearch,
Adzuna, Jooble). When any paid pool runs low, the company share rises automatically, so the portals get fewer searches.

One "search" = one keyword-and-city query to a portal, or one company career board.
"""
import json
import time

import requests

from . import config
from .sources import aggregators, apify

GROUP_OF = {"apify": "linkedin", "naukri": "naukri", "indeed": "naukri", "jsearch": "portals", "adzuna": "portals", "jooble": "portals"}
GROUP_LABEL = {"company": "Company sites", "linkedin": "LinkedIn", "naukri": "Naukri & Indeed", "portals": "Other portals"}
SERVICE_SPLIT = {"naukri": {"naukri": 0.6, "indeed": 0.4}, "portals": {"adzuna": 0.45, "jsearch": 0.35, "jooble": 0.2},
                 "linkedin": {"apify": 1.0}}
DEFAULT_WEIGHTS = {"linkedin": 0.34, "naukri": 0.33, "portals": 0.33}
FIRECRAWL_PER_SEARCH = 6          # one search page + a few job pages, on average
FIRECRAWL_RESERVE, JSEARCH_RESERVE, JOOBLE_RESERVE, APIFY_RESERVE_USD = 80, 10, 20, 0.5
ADZUNA_MAX_PER_SCAN = 25
_cache = {"at": 0, "snap": None}


def group_of_label(label):
    """Which group a scan source label belongs to ('Amazon (amazon)' is a company site)."""
    prefix = label.split(":")[0].strip().lower() if ":" in label else ""
    return GROUP_OF.get(prefix, "company")


def snapshot(refresh=True, max_age=120):
    """Credits per paid service: {name: {"remaining": x, "limit": y, "unit": ..., "fraction": 0-1}} for configured services only."""
    if not refresh and _cache["snap"]:
        return _cache["snap"]
    if _cache["snap"] and time.time() - _cache["at"] < max_age:
        return _cache["snap"]
    integ, snap = config.integrations(), {}
    if integ.get("naukri"):
        try:
            r = requests.get("https://api.firecrawl.dev/v2/team/credit-usage", timeout=20,
                             headers={"Authorization": f"Bearer {config.secret('FIRECRAWL_API_KEY')}"}).json()["data"]
            snap["firecrawl"] = {"remaining": r["remainingCredits"], "limit": r["planCredits"], "unit": "credits", "resets": r.get("billingPeriodEnd", "")[:10]}
        except Exception:
            pass
    if integ.get("jsearch"):
        try:
            q = json.loads((config.DATA_DIR / "jsearch_quota.json").read_text())
            snap["jsearch"] = {"remaining": q["remaining"], "limit": q["limit"], "unit": "searches"}
        except Exception:
            snap["jsearch"] = {"remaining": 200, "limit": 200, "unit": "searches", "estimated": True}
    if integ.get("apify"):
        u = apify.usage()
        if u:
            snap["apify"] = {"remaining": round(u["limit"] - u["used"], 2), "limit": u["limit"], "unit": "USD"}
    if integ.get("jooble"):
        used = aggregators.jooble_used()
        snap["jooble"] = {"remaining": aggregators.JOOBLE_LIFETIME - used, "limit": aggregators.JOOBLE_LIFETIME, "unit": "requests (lifetime)"}
    for v in snap.values():
        v["fraction"] = round(v["remaining"] / v["limit"], 3) if v["limit"] else 1.0
    _cache.update(at=time.time(), snap=snap)
    try:
        (config.DATA_DIR / "credits.json").write_text(json.dumps(snap))
    except OSError:
        pass
    return snap


def effective_share(s, snap):
    """Company share of searches. Configured value, raised automatically when any paid pool is running low."""
    c = min(0.95, max(0.3, float(s.get("company_share", 0.5))))
    if s.get("auto_balance", True):
        paid = [v["fraction"] for k, v in snap.items() if k in ("firecrawl", "jsearch", "apify")]
        low = min(paid) if paid else 1.0
        c = max(c, 0.85 if low < 0.15 else 0.7 if low < 0.30 else 0.6 if low < 0.50 else 0)
    return c


def allocate(company_units, share, s, snap, integ):
    """Searches per service for one scan. Returns {service: n}."""
    portal_units = round(company_units * (1 - share) / share)
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(s.get("group_weights") or {})
    usable = {}                                                    # service -> max searches the budget allows
    if integ.get("apify") and "apify" in snap:
        per_scan = float(s.get("apify_max_usd_per_scan", 0.6))
        room = min(per_scan, snap["apify"]["remaining"] - APIFY_RESERVE_USD)
        usable["apify"] = max(0, int(room / apify.query_cost_usd()))
    if integ.get("naukri") and "firecrawl" in snap:
        n = max(0, int((snap["firecrawl"]["remaining"] - FIRECRAWL_RESERVE) / FIRECRAWL_PER_SEARCH))
        usable["naukri"] = usable["indeed"] = n
    if integ.get("jsearch"):
        usable["jsearch"] = max(0, snap.get("jsearch", {"remaining": 200})["remaining"] - JSEARCH_RESERVE)
    if integ.get("adzuna"):
        usable["adzuna"] = ADZUNA_MAX_PER_SCAN
    if integ.get("jooble") and "jooble" in snap:
        usable["jooble"] = max(0, min(4, snap["jooble"]["remaining"] - JOOBLE_RESERVE))
    groups = {g: {sv: w for sv, w in split.items() if usable.get(sv, 0) > 0} for g, split in SERVICE_SPLIT.items()}
    groups = {g: sp for g, sp in groups.items() if sp}
    total_w = sum(weights.get(g, 0) for g in groups) or 1
    plan = {}
    for g, split in groups.items():
        units = portal_units * weights.get(g, 0) / total_w
        wsum = sum(split.values())
        for sv, w in split.items():
            plan[sv] = int(units * w / wsum)
    leftover = 0
    for sv in list(plan):                                          # budget caps
        if plan[sv] > usable[sv]:
            leftover += plan[sv] - usable[sv]
            plan[sv] = usable[sv]
    if leftover and "adzuna" in usable:                            # Adzuna is free: it absorbs searches other services cannot afford
        extra = min(leftover, usable["adzuna"] - plan.get("adzuna", 0))
        plan["adzuna"] = plan.get("adzuna", 0) + max(0, extra)
    return {k: v for k, v in plan.items() if v > 0}


def mix_report(summary_sources):
    """Searches and jobs kept per group from a finished scan's per-source results."""
    out = {g: {"searches": 0, "jobs": 0} for g in GROUP_LABEL}
    for label, v in (summary_sources or {}).items():
        g = group_of_label(label)
        out[g]["searches"] += 1
        out[g]["jobs"] += v.get("kept", 0)
    total = sum(v["searches"] for v in out.values()) or 1
    for v in out.values():
        v["share"] = round(100 * v["searches"] / total)
    return out
