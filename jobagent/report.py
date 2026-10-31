"""Dashboard statistics and the weekly performance report."""
import html
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import config, db
from .keywords import FUNCTIONS


def _since(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def weekly(days=7):
    s = _since(days)
    prof = config.profile()
    tgt = prof["weekly_application_target"]
    jobs_new = db.query("SELECT * FROM jobs WHERE first_seen >= ? AND status NOT IN ('duplicate', 'archived')", (s,))
    applied = db.query("SELECT * FROM jobs WHERE applied_at >= ?", (s,))
    actions = db.query("SELECT * FROM actions WHERE executed_at >= ? AND status = 'done'", (s,))
    emails = [a for a in actions if a["kind"] in ("email", "followup_email")]
    li = [a for a in actions if a["kind"] in ("linkedin_note", "linkedin_message")]
    replies = db.query("SELECT * FROM actions WHERE replied_at >= ?", (s,))
    all_sent = db.query("SELECT * FROM actions WHERE kind IN ('email','followup_email','linkedin_note','linkedin_message') AND status = 'done'")
    reply_rate = round(100 * sum(1 for a in all_sent if a["replied_at"]) / len(all_sent), 1) if all_sent else None
    interviews = db.query("SELECT * FROM events WHERE kind = 'status_changed' AND detail LIKE '%interview%' AND created_at >= ?", (s,))
    pipeline = Counter(r["status"] for r in db.query("SELECT status FROM jobs WHERE status NOT IN ('duplicate','archived')"))
    due = db.query("SELECT a.*, j.company, j.title FROM actions a JOIN jobs j ON j.id = a.job_id "
                   "WHERE a.kind = 'followup_email' AND a.status = 'draft' AND a.due_at <= ? ORDER BY a.due_at",
                   (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
    pending = db.one("SELECT COUNT(*) n FROM actions WHERE status IN ('draft','approved','executing','failed') AND (due_at IS NULL OR due_at <= ?)",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"),))["n"]
    top_open = db.query("SELECT id, company, title, city, score, posted_at, verification_status FROM jobs "
                        "WHERE status IN ('new','shortlisted','docs_ready') AND verification_status IN ('verified_open','likely_open') "
                        "ORDER BY score + CASE WHEN source_type = 'employer' THEN 8 ELSE 0 END DESC LIMIT 10")
    by_fn = Counter(FUNCTIONS.get(j["function"], {}).get("label", "Other") for j in applied)
    by_source = Counter(j["publisher"] or j["source"] for j in jobs_new)
    n_app = len(applied)
    status = "on track" if tgt["min"] <= n_app <= tgt["max"] else "below target" if n_app < tgt["min"] else "above target"
    recs = []
    if n_app < tgt["min"]:
        gap = tgt["min"] - n_app
        recs.append(f"Apply to {gap} more role{'s' if gap > 1 else ''} to reach the weekly minimum of {tgt['min']}.")
    if top_open:
        recs.append(f"{len(top_open)} verified, unapplied roles are waiting; highest is {top_open[0]['company']} – {top_open[0]['title']} ({int(top_open[0]['score'])}).")
    if due:
        recs.append(f"{len(due)} follow-up email{'s are' if len(due) > 1 else ' is'} due.")
    if all_sent and reply_rate is not None and reply_rate < 15 and len(all_sent) >= 10:
        recs.append("Reply rate is under 15%: favour hiring managers over generic mailboxes and personalise the opener.")
    if not any(config.integrations()[k] for k in ("jsearch", "adzuna", "naukri")):
        recs.append("Portal coverage is limited to employer sites: add a JSearch, Adzuna or Firecrawl key in .env.")
    return {
        "period_days": days, "from": s, "generated_at": db.now(),
        "jobs_discovered": len(jobs_new),
        "jobs_shortlisted": sum(1 for j in jobs_new if (j["score"] or 0) >= config.settings()["min_score_to_shortlist"]),
        "applications": n_app, "application_target": tgt, "target_status": status,
        "emails_sent": len(emails), "linkedin_sent": len(li), "replies": len(replies), "reply_rate_all_time": reply_rate,
        "interviews": len(interviews), "pipeline": dict(pipeline), "followups_due": due, "pending_approvals": pending,
        "by_function": dict(by_fn), "by_source": dict(by_source.most_common(12)), "top_open": top_open,
        "applied_list": [{"company": j["company"], "title": j["title"], "applied_at": j["applied_at"]} for j in applied],
        "recommendations": recs,
    }


def export_html(days=7):
    r = weekly(days)
    e = html.escape
    rows = "".join(f"<tr><td>{e(j['company'])}</td><td>{e(j['title'])}</td><td>{e((j['applied_at'] or '')[:10])}</td></tr>"
                   for j in r["applied_list"]) or "<tr><td colspan=3>No applications this week</td></tr>"
    top = "".join(f"<tr><td>{int(j['score'])}</td><td>{e(j['company'])}</td><td>{e(j['title'])}</td><td>{e(j['city'] or '')}</td></tr>"
                  for j in r["top_open"])
    kv = lambda d: "".join(f"<li>{e(str(k))}: {v}</li>" for k, v in d.items()) or "<li>None</li>"
    recs = "".join(f"<li>{e(x)}</li>" for x in r["recommendations"]) or "<li>No actions needed.</li>"
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>Weekly job search report</title><style>
    body{{font-family:Segoe UI,Arial,sans-serif;max-width:860px;margin:32px auto;padding:0 16px;color:#1d2433}}
    .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
    .kpi{{border:1px solid #dde3ee;border-radius:8px;padding:12px}} .kpi b{{display:block;font-size:26px}}
    table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid #e5e9f2;padding:6px;text-align:left}}
    h2{{margin-top:28px}}</style></head><body>
    <h1>Weekly job search report</h1><p>{e(r['from'][:10])} to {e(r['generated_at'][:10])}</p>
    <div class="kpis">
      <div class="kpi"><b>{r['applications']}</b>Applications (target {r['application_target']['min']}-{r['application_target']['max']}, {e(r['target_status'])})</div>
      <div class="kpi"><b>{r['jobs_discovered']}</b>Fresh jobs found</div>
      <div class="kpi"><b>{r['emails_sent'] + r['linkedin_sent']}</b>Outreach sent</div>
      <div class="kpi"><b>{r['replies']}</b>Replies</div>
      <div class="kpi"><b>{r['interviews']}</b>Interviews</div>
    </div>
    <h2>Next actions</h2><ul>{recs}</ul>
    <h2>Applications</h2><table><tr><th>Company</th><th>Role</th><th>Applied</th></tr>{rows}</table>
    <h2>Best open roles not yet applied</h2><table><tr><th>Score</th><th>Company</th><th>Role</th><th>City</th></tr>{top}</table>
    <h2>Pipeline</h2><ul>{kv(r['pipeline'])}</ul>
    <h2>Where jobs came from</h2><ul>{kv(r['by_source'])}</ul>
    </body></html>"""
    path = config.REPORTS_DIR / f"weekly_{datetime.now().strftime('%Y-%m-%d')}.html"
    path.write_text(doc, encoding="utf-8")
    return path
