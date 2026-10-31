"""Discovery pipeline: fetch -> filter (relevance, India, freshness) -> dedupe -> store -> score -> verify."""
import hashlib
import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, db, mix, resumes, scoring, verify
from .keywords import is_relevant_title
from .sources import aggregators, employers, firecrawl
from .sources.common import age_days, dedupe_key, is_india, pick_city

state = {"running": False, "progress": [], "summary": None, "started_at": None,
         "total": 0, "done": 0, "label": "", "phase": "idle", "stop": False, "stopped": False}
_lock = threading.Lock()


def _id(job):
    return hashlib.sha1(f"{job['source']}:{job['company']}:{job['external_id']}".encode()).hexdigest()[:16]


def accept(job, s):
    """Apply the hard filters. Returns a rejection reason or None."""
    if not is_relevant_title(job["title"]):
        return "not a target role"
    if s.get("india_only", True) and not is_india(job.get("location"), job.get("country")):
        if not (job.get("remote") and s.get("accept_remote_india") and is_india(job.get("description", "")[:3000])):
            return "outside India"
    age = age_days(job.get("posted_at"))
    if age is None:
        if s.get("require_posted_date", True):
            return "no posting date"
    elif age > s["max_job_age_days"]:
        return "older than limit"
    return None


def enrich(job):
    """Score, choose a resume. Mutates and returns job."""
    prof = config.profile()
    job["city"] = job.get("city") or pick_city(job.get("location", ""), prof["preferred_locations"])
    sc = scoring.score_job(job)
    key, reason = resumes.select_resume(sc["function"], job.get("description"), job["title"])
    job.update({"function": sc["function"], "score": sc["score"], "score_breakdown": json.dumps(sc),
                "resume_key": key, "resume_reason": reason})
    return job


def store(job, verify_now=False):
    """Insert or refresh a job. Returns (id, is_new)."""
    job = enrich(dict(job))
    job_id = job.get("id") or _id(job)
    job["dedupe_key"] = dedupe_key(job["company"], job["title"], job.get("city"))
    existing = db.one("SELECT id, source_type FROM jobs WHERE id = ?", (job_id,))
    if not existing:
        dup = db.one("SELECT id, source_type FROM jobs WHERE dedupe_key = ? AND id != ?", (job["dedupe_key"], job_id))
        if dup:
            # Same role seen elsewhere. Keep the employer copy as the primary record.
            if dup["source_type"] == "employer" or job["source_type"] != "employer":
                db.update("jobs", "id", dup["id"], {"last_seen": db.now()})
                return dup["id"], False
            db.update("jobs", "id", dup["id"], {"status": "duplicate", "notes": f"Duplicate of {job_id}"})
    cols = ["source", "source_type", "publisher", "external_id", "company", "company_domain", "title", "location",
            "city", "remote", "url", "apply_url", "description", "posted_at", "valid_through", "salary_min_lpa",
            "salary_max_lpa", "salary_text", "function", "score", "score_breakdown", "resume_key", "resume_reason",
            "dedupe_key"]
    row = {c: job.get(c) for c in cols}
    row["raw"] = json.dumps(job.get("raw")) if job.get("raw") else None
    if existing:
        db.update("jobs", "id", job_id, {**row, "last_seen": db.now()})
        is_new = False
    else:
        db.insert("jobs", {"id": job_id, **row, "first_seen": db.now(), "last_seen": db.now(), "status": "new"})
        db.log_event(job_id, "discovered", job["source"])
        is_new = True
    if verify_now or job["source_type"] == "employer":
        verify.verify_job(job_id, fetch_page=verify_now)
    return job_id, is_new


def next_queries(source, s, limit=None):
    """Keyword x city pairs for a paid/quota'd API, capped per scan and rotated so successive scans cover
    every pair. Pairs are interleaved so even one scan touches every city."""
    pairs = [(kw, city) for kw in s["search_keywords"] for city in s["scan_cities"]]
    pairs.sort(key=lambda p: (s["search_keywords"].index(p[0]) + s["scan_cities"].index(p[1])) % len(s["scan_cities"]))
    cap = int(limit) if limit else int(s.get(f"{source}_max_searches_per_scan", s.get("aggregator_max_requests_per_scan", 20)))
    if len(pairs) <= cap:
        return pairs
    cursor_file = config.DATA_DIR / "aggregator_cursor.json"
    try:
        cursors = json.loads(cursor_file.read_text())
    except (OSError, ValueError):
        cursors = {}
    start = cursors.get(source, 0) % len(pairs)
    batch = (pairs + pairs)[start:start + cap]
    cursors[source] = (start + cap) % len(pairs)
    cursor_file.write_text(json.dumps(cursors))
    return batch


EMPLOYER_BONUS = 8   # company-website listings outrank the same score from a portal


def priority(job):
    """Sort key: a job posted on the employer's own site beats a portal copy of equal fit."""
    return (job.get("score") or 0) + (EMPLOYER_BONUS if job.get("source_type") == "employer" else 0)


def discover_company_sites(limit=15):
    """For strong portal jobs whose employer is not on our list, look for the employer's own careers board
    (Greenhouse / Lever / Ashby / SmartRecruiters / Workday). If one exists it is added to the scan list and read
    straight away, so the company-website copy replaces the portal copy."""
    checked_file = config.DATA_DIR / "ats_checked.json"
    try:
        checked = json.loads(checked_file.read_text())
    except (OSError, ValueError):
        checked = {}
    known = {c["name"].lower() for c in config.companies()}
    rows = db.query("SELECT DISTINCT company, company_domain FROM jobs WHERE source_type = 'aggregator' AND score >= ? "
                    "AND status IN ('new', 'shortlisted', 'docs_ready') ORDER BY score DESC", (config.settings()["min_score_to_shortlist"],))
    found, tried = [], 0
    for r in rows:
        name, domain = r["company"], (r["company_domain"] or "").lower()
        if tried >= limit:
            break
        if not domain or name.lower() in known or checked.get(domain):
            continue
        tried += 1
        ats = None
        for path in ("/careers", "/jobs", "/careers/"):
            try:
                ats = employers.detect_ats(f"https://{domain}{path}")
            except Exception:
                ats = None
            if ats:
                break
        checked[domain] = ats["ats"] if ats else "none"
        if not ats:
            continue
        entry = {"name": name, **ats, "domain": domain, "auto": True}
        items = config.companies()
        items.append(entry)
        config.save_companies(items)
        known.add(name.lower())
        found.append(name)
        try:
            s = config.settings()
            kw = {"max_age_days": s["max_job_age_days"]} if ats["ats"] == "workday" else {}
            for job in employers.FETCHERS[ats["ats"]](entry, **kw):
                if not accept(job, s):
                    store(job)
        except Exception:
            pass
    checked_file.write_text(json.dumps(checked))
    return found


def _progress(msg):
    state["progress"].append({"at": db.now(), "msg": msg})
    state["progress"] = state["progress"][-200:]


def run_scan(include_employers=True, include_aggregators=True):
    with _lock:
        if state["running"]:
            return {"error": "A scan is already running"}
        state.update({"running": True, "progress": [], "summary": None, "started_at": db.now(), "total": 0, "done": 0,
                      "label": "Getting ready", "phase": "Starting", "stop": False, "stopped": False})
    s = config.settings()
    scan_id = db.insert("scans", {"started_at": db.now()})
    summary = {"sources": {}, "new": 0, "updated": 0, "rejected": {}, "errors": []}
    seen_ids = {}
    try:
        tasks = []
        if include_employers:
            for c in config.companies():
                fn = employers.FETCHERS.get(c["ats"])
                if fn:
                    kwargs = {"max_age_days": s["max_job_age_days"]} if c["ats"] == "workday" else {}
                    tasks.append((f"{c['name']} ({c['ats']})", c, lambda c=c, fn=fn, kw=kwargs: fn(c, **kw)))
        if include_aggregators:
            integ = config.integrations()
            firecrawl.reset_budget()
            state.update({"phase": "Checking your credits", "label": ""})
            snap = mix.snapshot()
            share = mix.effective_share(s, snap)
            plan = mix.allocate(len(config.companies()), share, s, snap, integ)
            summary["plan"] = {"company_share": round(share, 2), "searches": plan}
            _progress(f"Mix: company sites {round(share * 100)}% of searches; portals {plan}")
            for name, n in plan.items():
                fn = aggregators.AGGREGATORS[name]
                for kw, city in next_queries(name, s, limit=n):
                    tasks.append((f"{name}: {kw} / {city}", None,
                                  lambda fn=fn, kw=kw, city=city: fn(kw, city, s["max_job_age_days"])))
        _progress(f"Scanning {len(tasks)} sources")
        state.update({"total": len(tasks) + (1 if include_aggregators else 0), "done": 0, "phase": "Searching for jobs"})

        def run_task(label, fn):
            if state["stop"]:
                return []
            state["label"] = label
            return fn()

        with ThreadPoolExecutor(8) as ex:
            futures = {ex.submit(run_task, t[0], t[2]): t for t in tasks}
            for fut in as_completed(futures):
                label, company, _ = futures[fut]
                state["done"] += 1
                if state["stop"]:
                    ex.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    jobs = fut.result()
                except Exception as e:
                    summary["errors"].append(f"{label}: {type(e).__name__}: {str(e)[:150]}")
                    _progress(f"{label}: error {type(e).__name__}")
                    continue
                kept = 0
                for job in jobs:
                    why = accept(job, s)
                    if why:
                        summary["rejected"][why] = summary["rejected"].get(why, 0) + 1
                        continue
                    try:
                        job_id, is_new = store(job)
                    except Exception as e:
                        summary["errors"].append(f"{label}: store failed: {e}")
                        continue
                    kept += 1
                    summary["new" if is_new else "updated"] += 1
                    if company:
                        seen_ids.setdefault(company["name"], set()).add(job_id)
                summary["sources"][label] = {"fetched": len(jobs), "kept": kept}
                if company:
                    seen_ids.setdefault(company["name"], set())
                _progress(f"{label}: {len(jobs)} relevant in India, {kept} fresh")
        state["stopped"] = state["stop"]
        # Employer listings that vanished from a feed we just read successfully are closed (only after a complete scan).
        for company, ids in ({} if state["stopped"] else seen_ids).items():
            for row in db.query("SELECT id FROM jobs WHERE company = ? AND source_type = 'employer' AND source != 'import' "
                                "AND verification_status != 'closed'", (company,)):
                if row["id"] not in ids:
                    db.update("jobs", "id", row["id"], {"verification_status": "closed",
                              "verification_notes": json.dumps({"status": "closed", "notes": ["No longer listed on the employer career system"], "flags": []}),
                              "verified_at": db.now()})
        if include_aggregators and not state["stopped"]:
            state.update({"phase": "Looking for company career pages", "label": "Checking employer websites"})
            try:
                found = discover_company_sites()
                if found:
                    _progress(f"Found company career sites for: {', '.join(found)}")
                    summary["company_sites_found"] = found
            except Exception as e:
                summary["errors"].append(f"company-site discovery: {type(e).__name__}: {str(e)[:100]}")
        # Auto-shortlist strong fits
        db.execute("UPDATE jobs SET status = 'shortlisted' WHERE status = 'new' AND score >= ? "
                   "AND verification_status NOT IN ('suspicious', 'closed')", (s["min_score_to_shortlist"],))
    except Exception:
        summary["errors"].append(traceback.format_exc()[-500:])
    finally:
        summary["stopped"] = state["stopped"]
        summary["mix"] = mix.mix_report(summary.get("sources"))
        db.update("scans", "id", scan_id, {"finished_at": db.now(), "summary": json.dumps(summary)})
        state.update({"done": state["total"] if not state["stopped"] else state["done"],
                      "phase": "Stopped" if state["stopped"] else "Complete", "label": "", "summary": summary})
        state["running"] = False
        _progress(f"Done: {summary['new']} new, {summary['updated']} refreshed, {len(summary['errors'])} errors")
    return summary


def start_background(**kw):
    if state["running"]:
        return False
    threading.Thread(target=run_scan, kwargs=kw, daemon=True).start()
    return True


def rescore_all():
    for job in db.query("SELECT * FROM jobs"):
        if not is_relevant_title(job["title"]) and job["status"] in ("new", "shortlisted"):
            db.update("jobs", "id", job["id"], {"status": "archived", "notes": "No longer matches target roles"})
            continue
        enrich(job)
        db.update("jobs", "id", job["id"], {k: job[k] for k in ("city", "function", "score", "score_breakdown",
                                                                "resume_key", "resume_reason")})
    threshold = config.settings()["min_score_to_shortlist"]
    db.execute("UPDATE jobs SET status = 'new' WHERE status = 'shortlisted' AND score < ?", (threshold,))
    db.execute("UPDATE jobs SET status = 'shortlisted' WHERE status = 'new' AND score >= ? "
               "AND verification_status NOT IN ('suspicious', 'closed')", (threshold,))
