"""Job fit score (0-100) against the candidate's experience, skills, locations and salary target.

function 30 + skills 25 + experience 15 + location 15 + salary 15. Every component returns a reason so the
score can be audited in the UI.
"""
import re

from . import config
from .keywords import FUNCTIONS, classify_job, is_senior_ic, is_too_junior, is_too_senior, skills_in
from .resumes import candidate_skills

EXP_RANGE = re.compile(r"(\d{1,2})\s*(?:\+\s*)?(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years|yrs|year)", re.I)
EXP_MIN = re.compile(r"(?:minimum|min\.?|at least|over)?\s*(\d{1,2})\s*\+?\s*(?:years|yrs|year)", re.I)


def required_experience(text):
    """(min, max) years from the JD, using the mention closest to the word 'experience'."""
    text = text or ""
    best = None
    for rx, kind in ((EXP_RANGE, "range"), (EXP_MIN, "min")):
        for m in rx.finditer(text):
            window = text[max(0, m.start() - 80): m.end() + 80].lower()
            if "experience" not in window and "exp" not in window:
                continue
            lo = int(m.group(1))
            hi = int(m.group(2)) if kind == "range" else None
            if lo > 20 or (hi and hi > 25):
                continue
            best = best or (lo, hi)
        if best:
            return best
    return None


def score_job(job):
    prof = config.profile()
    title = job["title"]
    desc = job.get("description") or ""
    text = f"{title}\n{desc}"
    parts = {}

    fn, strength = classify_job(title, desc)
    if fn is None:
        # fall back to the description for generic titles
        fn_desc = max(FUNCTIONS, key=lambda k: sum(len(re.findall(p, desc.lower())) for p in FUNCTIONS[k]["strong"]))
        fn, strength = fn_desc, "description"
    fn_pts = {"strong": 30, "weak": 18, "description": 10}[strength]
    parts["function"] = {"points": fn_pts, "max": 30,
                         "reason": f"{FUNCTIONS[fn]['label']} ({'title match' if strength != 'description' else 'from JD text'}"
                                   f"{', partial' if strength == 'weak' else ''})"}

    jd_sk = skills_in(text)
    have = candidate_skills()
    matched, missing = sorted(jd_sk & have), sorted(jd_sk - have)
    if len(jd_sk) < 3:
        sk_pts, sk_reason = 13, "JD lists few recognisable skills; neutral"
    else:
        cov = len(matched) / len(jd_sk)
        sk_pts = round(25 * min(1.0, cov * 1.15))
        sk_reason = f"{len(matched)}/{len(jd_sk)} JD skills on resume"
    parts["skills"] = {"points": sk_pts, "max": 25, "reason": sk_reason, "matched": matched, "missing": missing}

    cand = prof["total_experience_years"]
    req = required_experience(desc)
    if req is None and is_senior_ic(title):
        ex_pts, ex_reason = 8, "No stated requirement; senior titles usually expect 5-8 yrs"
    elif req is None:
        ex_pts, ex_reason = 10, "No experience requirement found"
    else:
        lo, hi = req
        if cand >= lo and (hi is None or cand <= hi):
            ex_pts, ex_reason = 15, f"Asks {lo}{'-' + str(hi) if hi else '+'} yrs; you have {cand}"
        elif cand < lo:
            gap = lo - cand
            ex_pts = 11 if gap <= 0.6 else 7 if gap <= 1.6 else 3 if gap <= 2.6 else 0
            ex_reason = f"Asks {lo}{'-' + str(hi) if hi else '+'} yrs; {gap:.1f} yrs short"
        else:
            over = cand - hi
            ex_pts = 12 if over <= 1.5 else 7
            ex_reason = f"Asks {lo}-{hi} yrs; you are {over:.1f} yrs above range"
    senior = is_too_senior(title)
    if senior:
        ex_pts = min(ex_pts, 3)
        ex_reason += "; title is senior leadership"
    parts["experience"] = {"points": ex_pts, "max": 15, "reason": ex_reason, "required": req}

    preferred = prof["preferred_locations"]
    city = job.get("city")
    if city in preferred:
        lc_pts, lc_reason = 15, f"{city} (preferred)"
    elif city in prof.get("open_locations", []):
        lc_pts, lc_reason = 12, f"{city} (open to it)"
    elif job.get("remote"):
        lc_pts, lc_reason = 12, "Remote in India"
    elif city == "Delhi NCR" and "Gurugram" in preferred:
        lc_pts, lc_reason = 9, "Delhi NCR (near Gurugram)"
    elif city:
        lc_pts, lc_reason = 4, f"{city} (not a preferred city)"
    else:
        lc_pts, lc_reason = 6, "Location unclear"
    parts["location"] = {"points": lc_pts, "max": 15, "reason": lc_reason}

    lo, hi = job.get("salary_min_lpa"), job.get("salary_max_lpa")
    tgt = prof["target_ctc_lpa"]
    cur = prof["current_ctc_lpa"]
    if lo is None and hi is None:
        sa_pts, sa_reason = 8, "Salary not disclosed"
    else:
        top = hi or lo
        if top < cur:
            sa_pts, sa_reason = 0, f"Up to {top} LPA is below current {cur} LPA"
        elif top < tgt["min"]:
            sa_pts, sa_reason = 5, f"Up to {top} LPA, below the {tgt['min']}-{tgt['max']} LPA target"
        elif lo and lo > tgt["max"] * 1.5:
            sa_pts, sa_reason = 8, f"{lo}+ LPA suggests a more senior band"
        else:
            sa_pts, sa_reason = 15, f"{lo or '?'}-{hi or '?'} LPA overlaps the {tgt['min']}-{tgt['max']} LPA target"
    parts["salary"] = {"points": sa_pts, "max": 15, "reason": sa_reason}

    total = sum(p["points"] for p in parts.values())
    caps = []
    if senior:
        total = min(total, 45)
        caps.append("Capped at 45: leadership title well above 4.4 yrs experience")
    if is_too_junior(title):
        total = min(total, 50)
        caps.append("Capped at 50: title is usually below your current level and 19 LPA CTC")
    parts["caps"] = caps
    label = "Strong" if total >= 75 else "Good" if total >= 60 else "Stretch" if total >= 45 else "Low"
    caps = parts.pop("caps")
    return {"score": total, "label": label, "function": fn, "parts": parts, "caps": caps}
