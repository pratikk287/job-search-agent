"""Resume parsing, selection, consistency checks and tailoring (facts only from the source resume)."""
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

from pypdf import PdfReader

from . import config
from .keywords import skills_in

HEADINGS = {"PROFILE", "PROFESSIONAL EXPERIENCE", "PRODUCT TRAINING & PROJECTS", "SKILLS", "EDUCATION", "INTERESTS"}
ROLE_RE = re.compile(
    r"^(?P<title>.+?),\s*(?P<company>.+?)\s+(?P<start>[A-Z][a-z]{2}\s\d{4})\s*[–-]\s*(?P<end>Present|[A-Z][a-z]{2}\s\d{4})$")
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
BULLET = "●"


def _pdf_text(path):
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _month(s):
    if s == "Present":
        today = date.today()
        return today.year, today.month
    mon, year = s.split()
    return int(year), MONTHS[mon]


def parse_resume_text(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()] or ["", ""]
    lines += [""] * (2 - len(lines))
    data = {"name": lines[0], "contact_line": lines[1], "profile": "", "experience": [], "projects_intro": "",
            "projects": [], "skills": [], "education": [], "interests": "", "full_text": text}
    section = None
    buf = []

    def flush_profile():
        if section == "PROFILE":
            data["profile"] = " ".join(buf).strip()

    for line in lines[2:]:
        if line in HEADINGS:
            flush_profile()
            section, buf = line, []
            continue
        if section == "PROFILE":
            buf.append(line)
        elif section == "PROFESSIONAL EXPERIENCE":
            m = ROLE_RE.match(line)
            if m:
                data["experience"].append({**m.groupdict(), "bullets": []})
            elif line.startswith(BULLET) and data["experience"]:
                data["experience"][-1]["bullets"].append(line.lstrip(BULLET).strip())
            elif data["experience"] and data["experience"][-1]["bullets"]:
                data["experience"][-1]["bullets"][-1] += " " + line
        elif section == "PRODUCT TRAINING & PROJECTS":
            if line.startswith(BULLET):
                data["projects"].append(line.lstrip(BULLET).strip())
            elif data["projects"]:
                data["projects"][-1] += " " + line
            else:
                data["projects_intro"] = (data["projects_intro"] + " " + line).strip()
        elif section == "SKILLS":
            if ":" in line and line.split(":")[0].count(" ") <= 4:
                cat, _, items = line.partition(":")
                data["skills"].append({"category": cat.strip(), "items": items.strip()})
            elif data["skills"]:
                data["skills"][-1]["items"] += " " + line
        elif section == "EDUCATION":
            data["education"].append(line)
        elif section == "INTERESTS":
            data["interests"] = (data["interests"] + " " + line).strip()
    flush_profile()
    for role in data["experience"]:
        role["is_internship"] = "intern" in role["title"].lower()
        (y1, m1), (y2, m2) = _month(role["start"]), _month(role["end"])
        role["months"] = (y2 - y1) * 12 + (m2 - m1)
    data["skills_detected"] = sorted(skills_in(text))
    return data


@lru_cache(maxsize=4)
def load(key):
    """Parsed resume A or B."""
    spec = config.profile()["resumes"][key]
    path = config.ROOT / spec["file"]
    missing = not path.exists()                      # fresh install: the app still starts; Settings asks for the PDFs
    parsed = parse_resume_text("" if missing else _pdf_text(path))
    parsed.update({"key": key, "label": spec["label"], "file": str(path), "functions": spec["functions"], "missing": missing})
    if missing:
        return parsed
    (config.DATA_DIR / f"resume_{key}.json").write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
    return parsed


def all_resumes():
    return {k: load(k) for k in config.profile()["resumes"]}


def candidate_skills():
    out = set()
    for r in all_resumes().values():
        out |= set(r["skills_detected"])
    return out


def consistency_issues():
    """Material differences between the stated profile and the resumes, to be clarified by the candidate."""
    prof = config.profile()
    res = all_resumes()
    issues = []
    absent = [r["file"] for r in res.values() if r.get("missing")]
    if absent:
        return [{"field": "resumes", "detail": "Add your resume PDF(s) to the resumes folder and set their file names in config/profile.json: "
                 + ", ".join(Path(a).name for a in absent) + ". Then restart the app.", "resolved": False}]
    titles = {k: r["experience"][0]["title"] for k, r in res.items() if r["experience"]}
    if len(set(titles.values())) > 1:
        issues.append({
            "field": "current_title",
            "detail": "The two resumes give different titles for the current role: "
                      + "; ".join(f"Resume {k}: '{t}'" for k, t in titles.items())
                      + ". Confirm the official title before it is used on application forms.",
            "resolved": bool(prof.get("current_title")),
        })
    a = next(iter(res.values()))
    full_time = [r for r in a["experience"] if not r["is_internship"]]
    months = sum(r["months"] for r in full_time)
    yrs = round(months / 12, 1)
    if abs(yrs - prof["total_experience_years"]) >= 0.2:
        issues.append({
            "field": "total_experience_years",
            "detail": f"Full-time roles on the resume add up to about {yrs} years "
                      f"({', '.join(r['company'] + ' ' + str(r['months']) + ' mo' for r in full_time)}); "
                      f"the profile says {prof['total_experience_years']}. Confirm which figure to state on forms.",
            "resolved": False,
        })
    current = full_time[0] if full_time else None
    if current and current["end"] == "Present":
        post = round(current["months"] / 12, 1)
        if abs(post - prof["post_mba_experience_years"]) >= 0.2:
            issues.append({
                "field": "post_mba_experience_years",
                "detail": f"Current role started {current['start']} (about {post} years ago); the profile says "
                          f"{prof['post_mba_experience_years']} years post-MBA.",
                "resolved": False,
            })
    confirmed = set(prof.get("confirmed_fields", []))
    for i in issues:
        if i["field"] in confirmed:
            i["resolved"] = True
    if not prof.get("linkedin_url"):
        issues.append({"field": "linkedin_url", "detail": "No LinkedIn URL in profile; application forms usually ask for it.",
                       "resolved": False})
    return issues


# ---------------------------------------------------------------- selection

def select_resume(function, description, title=""):
    """Pick resume A or B for a job. Returns (key, reason)."""
    jd = f"{title}\n{description or ''}"
    jd_skills = skills_in(jd)
    # A clear title ("Category Manager") decides the role family; a loose one ("Brand Manager") lets the JD decide.
    from .keywords import classify_title
    fn_weight = 0.6 if classify_title(title)[1] == "strong" else 0.2
    scores = {}
    for key, r in all_resumes().items():
        fn_match = 1.0 if function in r["functions"] else 0.0
        overlap = len(jd_skills & set(r["skills_detected"])) / max(len(jd_skills), 1)
        scores[key] = (fn_weight * fn_match + (1 - fn_weight) * overlap, fn_match, overlap)
    best = max(scores, key=lambda k: scores[k][0])
    s, fn_match, overlap = scores[best]
    label = config.profile()["resumes"][best]["label"]
    parts = []
    if fn_match:
        parts.append("is the resume for this role family")
    other = [k for k in scores if k != best]
    if other:
        parts.append(f"covers {overlap:.0%} of the JD's skills vs {scores[other[0]][2]:.0%} for Resume {other[0]}")
    return best, f"Resume {best} ({label}) " + " and ".join(parts) + "."


# ---------------------------------------------------------------- tailoring

def _relevance(text, jd_skills, jd_text):
    s = len(skills_in(text) & jd_skills) * 3
    for word in set(re.findall(r"[a-z]{5,}", text.lower())):
        if word in jd_text:
            s += 0.2
    return s


def tailor(key, job):
    """Tailored resume content: same facts, re-ordered and emphasised for the JD. Nothing is invented."""
    r = load(key)
    jd_text = f"{job['title']} {job.get('description') or ''}".lower()
    jd_skills = skills_in(jd_text)
    experience = []
    for role in r["experience"]:
        bullets = sorted(role["bullets"], key=lambda b: -_relevance(b, jd_skills, jd_text))
        experience.append({**role, "bullets": bullets})
    skills = []
    for line in r["skills"]:
        items = [i.strip() for i in re.split(r",(?![^()]*\))", line["items"]) if i.strip()]
        items.sort(key=lambda i: -_relevance(i, jd_skills, jd_text))
        skills.append({"category": line["category"], "items": items,
                       "rel": sum(_relevance(i, jd_skills, jd_text) for i in items)})
    skills.sort(key=lambda s: -s["rel"])
    projects = sorted(r["projects"], key=lambda p: -_relevance(p, jd_skills, jd_text))
    matched = sorted(jd_skills & set(r["skills_detected"]))
    missing = sorted(jd_skills - candidate_skills())
    title = config.profile().get("current_title") or r["experience"][0]["title"]
    return {
        "key": key, "name": r["name"], "contact_line": r["contact_line"], "profile": r["profile"],
        "experience": experience, "projects_intro": r["projects_intro"], "projects": projects,
        "skills": skills, "education": r["education"], "interests": r["interests"],
        "current_title": title, "matched_skills": matched, "missing_skills": missing,
    }


def achievements(key, job, n=3):
    """Top-n resume bullets most relevant to the job (verbatim)."""
    t = tailor(key, job)
    pool = []
    for role in t["experience"]:
        for b in role["bullets"]:
            pool.append((role, b))
    jd_text = f"{job['title']} {job.get('description') or ''}".lower()
    jd_skills = skills_in(jd_text)

    def recency(role):
        if role["end"] == "Present":
            return 2
        return 1 if _month(role["end"])[0] >= date.today().year - 4 else -1

    def value(rb):
        has_metric = 1.5 if re.search(r"\d+\s*(%|x\b)|~\d+", rb[1]) else 0
        return _relevance(rb[1], jd_skills, jd_text) + recency(rb[0]) + has_metric

    pool.sort(key=lambda rb: -value(rb))
    picked, per_role = [], {}
    for role, b in pool:        # at most two per role, so a quantified result from another role gets in
        k = (role["title"], role["start"])
        if per_role.get(k, 0) < 2:
            picked.append((role, b))
            per_role[k] = per_role.get(k, 0) + 1
        if len(picked) == n:
            break
    return [{"role": f"{r['title']}, {r['company']}", "bullet": b} for r, b in picked]


def resume_numbers(key):
    """Every number/metric that appears in a resume; used to reject generated text that invents metrics."""
    return set(re.findall(r"\d+(?:\.\d+)?", load(key)["full_text"]))
