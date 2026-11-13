"""Mock interviews with Claude: an interviewer that asks one question at a time, probes vague answers, and at the
end scores the candidate and shows how to improve each answer. Sessions are kept in the database."""
import json

from . import config, db, llm, resumes

KINDS = {
    "hr": ("HR screening round", "a talent-acquisition partner",
           "background walk-through, motivation for the switch, why this company, current and expected CTC, notice period, location"),
    "role": ("Hiring manager round", "the hiring manager for this role",
             "deep questions on the skills this role needs, how the candidate did similar work, numbers and trade-offs, one short scenario"),
    "behavioral": ("Behavioural round", "a senior manager on the team",
                   "STAR-style questions: a launch, a failure, a conflict, influence without authority, using data, a tight deadline"),
    "case": ("Case and guesstimate round", "a product or strategy lead",
             "one business case and one market-size estimate relevant to the company; ask for structure first, then numbers"),
    "salary": ("Salary negotiation role-play", "the recruiter making the offer",
               "you have chosen the candidate; open with an offer about 10% below their expected CTC with a mostly variable mix, "
               "push back once on budget, and respond realistically to good or weak negotiation"),
    "remote": ("Remote job abroad round", "a hiring manager at a remote-first company abroad",
               "remote work habits, written communication, ownership without supervision, time-zone overlap, contractor setup, plus 2-3 role questions"),
}
QUESTIONS = {"hr": 6, "role": 7, "behavioral": 6, "case": 4, "salary": 5, "remote": 6}
START = "(The candidate has joined the call. Greet them in one short line and ask your first question.)"
END = "[END]"

FEEDBACK_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_score": {"type": "integer"},
        "verdict": {"type": "string"},
        "scores": {"type": "array", "items": {"type": "object", "properties": {
            "area": {"type": "string"}, "score": {"type": "integer"}, "comment": {"type": "string"}},
            "required": ["area", "score", "comment"], "additionalProperties": False}},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "improve": {"type": "array", "items": {"type": "string"}},
        "answers": {"type": "array", "items": {"type": "object", "properties": {
            "question": {"type": "string"}, "what_worked": {"type": "string"}, "what_to_fix": {"type": "string"},
            "better_answer": {"type": "string"}}, "required": ["question", "what_worked", "what_to_fix", "better_answer"],
            "additionalProperties": False}},
        "next_practice": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overall_score", "verdict", "scores", "strengths", "improve", "answers", "next_practice"],
    "additionalProperties": False,
}


def _context(job, role, company):
    prof = config.profile()
    key = (job or {}).get("resume_key") or "A"
    res = resumes.load(key)
    cur = prof.get("current_ctc") or {}
    cand = (f"Name: {prof.get('name')}. Current: {prof.get('current_title')} at {prof.get('current_employer')}, "
            f"{prof.get('total_experience_years')} years of experience. Current CTC {cur.get('total_lpa')} LPA "
            f"({cur.get('fixed_lpa')} fixed + {cur.get('variable_lpa')} variable), expected {prof.get('expected_ctc_lpa')} LPA, "
            f"notice {prof.get('notice_period')}.")
    jd = (job or {}).get("description") or ""
    return cand, res["full_text"][:7000], jd[:7000]


def _system(kind, role, company, coach, job):
    label, persona, focus = KINDS[kind]
    cand, resume, jd = _context(job, role, company)
    first = (config.profile().get("first_name") or "the candidate")
    coach_line = ("After each answer, first write one line starting with 'Tip:' that says the single most useful improvement "
                  "to that answer, then on a new line ask the next question." if coach else
                  "Do not give feedback, praise or scores during the interview; react briefly like a real interviewer.")
    return (f"You are {persona} at {company}, running a {label} with {first} for the role of {role}. "
            f"Cover: {focus}. Ask exactly one question per turn and keep each turn short (1-3 sentences), as in a spoken interview. "
            "If an answer is vague, ask one follow-up for specifics: their own part, numbers, the result. "
            f"After about {QUESTIONS[kind]} main questions, close politely and end that message with {END}. "
            f"{coach_line} Stay in character. If the candidate asks you something, answer briefly as this interviewer would. "
            "Plain text only, no markdown, no em dashes. Base questions on the job description and the resume below; "
            "the candidate is in India, so use Indian conventions (LPA, notice period).\n\n"
            f"CANDIDATE: {cand}\n\nRESUME:\n{resume}\n\nJOB DESCRIPTION:\n{jd or '(none given: use a typical one for this role)'}")


def _out(row):
    msgs = json.loads(row["messages"] or "[]")
    return {"id": row["id"], "kind": row["kind"], "kind_label": KINDS[row["kind"]][0], "role": row["role"], "company": row["company"],
            "job_id": row["job_id"], "coach": bool(row["coach"]), "created_at": row["created_at"], "finished_at": row["finished_at"],
            "ended": bool(msgs) and msgs[-1]["role"] == "assistant" and END in msgs[-1]["content"],
            "messages": [{"role": m["role"], "text": m["content"].replace(END, "").strip()} for m in msgs[1:]],
            "feedback": json.loads(row["feedback"]) if row["feedback"] else None}


def get(iid):
    row = db.one("SELECT * FROM interviews WHERE id = ?", (iid,))
    return _out(row) if row else None


def history():
    return [{k: v for k, v in _out(r).items() if k not in ("messages",)} | {"turns": len(json.loads(r["messages"] or "[]")) // 2}
            for r in db.query("SELECT * FROM interviews ORDER BY id DESC LIMIT 30")]


def start(kind, job_id=None, role=None, company=None, coach=False):
    if kind not in KINDS:
        raise ValueError("Unknown interview type")
    if not llm.available():
        raise RuntimeError("The mock interviewer needs Claude. Add ANTHROPIC_API_KEY to .env and restart the app.")
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,)) if job_id else None
    role = (job or {}).get("title") or role or config.profile().get("current_title") or "this role"
    company = (job or {}).get("company") or company or "a fast-growing company in India"
    msgs = [{"role": "user", "content": START}]
    first = llm.chat(_system(kind, role, company, coach, job), msgs, effort="low")
    if not first:
        raise RuntimeError("Claude did not answer. Check your internet connection and API key, then try again.")
    msgs.append({"role": "assistant", "content": first})
    iid = db.insert("interviews", {"job_id": job_id, "kind": kind, "role": role, "company": company, "coach": int(bool(coach)),
                                   "messages": json.dumps(msgs), "created_at": db.now()})
    return get(iid)


def answer(iid, text):
    row = db.one("SELECT * FROM interviews WHERE id = ?", (iid,))
    if not row:
        raise ValueError("Interview not found")
    msgs = json.loads(row["messages"])
    if msgs[-1]["role"] == "assistant" and END in msgs[-1]["content"]:
        raise ValueError("This interview has ended. Press 'Get feedback'.")
    job = db.one("SELECT * FROM jobs WHERE id = ?", (row["job_id"],)) if row["job_id"] else None
    msgs.append({"role": "user", "content": (text or "").strip() or "(no answer)"})
    reply = llm.chat(_system(row["kind"], row["role"], row["company"], row["coach"], job), msgs, effort="low")
    if not reply:
        raise RuntimeError("Claude did not answer. Try sending your answer again.")
    msgs.append({"role": "assistant", "content": reply})
    db.update("interviews", "id", iid, {"messages": json.dumps(msgs)})
    return get(iid)


def finish(iid):
    row = db.one("SELECT * FROM interviews WHERE id = ?", (iid,))
    if not row:
        raise ValueError("Interview not found")
    msgs = json.loads(row["messages"])[1:]
    if not any(m["role"] == "user" for m in msgs):
        raise ValueError("Answer at least one question first.")
    job = db.one("SELECT * FROM jobs WHERE id = ?", (row["job_id"],)) if row["job_id"] else None
    cand, resume, jd = _context(job, row["role"], row["company"])
    transcript = "\n\n".join(f"{'INTERVIEWER' if m['role'] == 'assistant' else 'CANDIDATE'}: {m['content'].replace(END, '').strip()}" for m in msgs)
    system = ("You are a demanding but kind interview coach in India. Score honestly: 5 is average, 7 is a likely pass, 9 is exceptional. "
              "In better_answer, rewrite the candidate's answer in first person using only facts from the RESUME and what they said; "
              "never invent numbers or results (write [your number] where a metric would help). Plain English, no em dashes.")
    prompt = (f"{KINDS[row['kind']][0]} for {row['role']} at {row['company']}.\n\nCANDIDATE: {cand}\n\nRESUME:\n{resume}\n\n"
              f"JOB DESCRIPTION:\n{jd or '(none)'}\n\nTRANSCRIPT:\n{transcript}\n\n"
              "Give feedback: overall score out of 10, a one-line verdict, scores out of 10 for Structure, Specificity and numbers, "
              "Role knowledge, Communication and Confidence (each with one comment), 2-4 strengths, 3-5 improvements, "
              "per-question feedback for every question the candidate answered, and 3 things to practise next.")
    fb = llm.structured(system, prompt, FEEDBACK_SCHEMA, max_tokens=12000, effort="medium")
    if not fb:
        raise RuntimeError("Could not get feedback from Claude. Try again.")
    db.update("interviews", "id", iid, {"feedback": json.dumps(fb), "finished_at": db.now()})
    return get(iid)


def delete(iid):
    db.execute("DELETE FROM interviews WHERE id = ?", (iid,))
