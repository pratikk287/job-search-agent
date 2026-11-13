"""Interview prep: short crash courses (salary negotiation, the common interview questions, one per role family,
case questions, remote jobs abroad) and a prep brief for one job. Courses are written here, then personalised with the
candidate's own numbers and resume; the per-job brief uses Claude when it is connected and a template otherwise."""
import json
import re

from . import config, db, llm, resumes
from .keywords import skills_in


def _money(v):
    return f"{v:g} LPA" if isinstance(v, (int, float)) else str(v or "?")


def _salary_course():
    p = config.profile()
    cur = p.get("current_ctc") or {}
    total, fixed, var = cur.get("total_lpa") or p.get("current_ctc_lpa"), cur.get("fixed_lpa"), cur.get("variable_lpa")
    tgt = p.get("target_ctc_lpa") or {}
    lo, hi, exp = tgt.get("min"), tgt.get("max"), p.get("expected_ctc_lpa")
    hike = f"{round((exp / total - 1) * 100)}%" if exp and total else "?"
    ask_hi = round(hi * 1.08) if hi else None
    return {
        "id": "salary", "title": "Salary negotiation", "minutes": 12, "tag": "Money",
        "summary": f"Your numbers: current {_money(total)} ({_money(fixed)} fixed + {_money(var)} variable), "
                   f"expected {_money(exp)} ({hike} hike), target range {_money(lo)} to {_money(hi)}.",
        "sections": [
            {"h": "Before any call", "points": [
                "Know three numbers: your walk-away (the lowest you accept), your target, and your opening ask (a bit above target).",
                f"For you: walk-away about {_money(lo)}, target {_money(exp)}, opening ask about {_money(ask_hi)} fixed-heavy.",
                "Split CTC into fixed, variable, joining bonus, ESOPs and benefits. Compare offers on fixed pay first; variable is often paid at 60-80%.",
                "Look up the band: ask friends at the company, AmbitionBox, Glassdoor, Levels.fyi, and recruiters on LinkedIn.",
                "In India a switch usually brings a 20-40% hike on fixed. Above 40% needs a clear reason: a bigger scope, a competing offer, or a scarce skill."]},
            {"h": "When they ask 'What is your expected CTC?'", "points": [
                "Early in the process, give a range anchored on fixed pay and tie it to the role, not to your current salary.",
                f"Say: \"For a role with this scope I'm looking at {_money(lo)} to {_money(hi)}, with most of it fixed. I'm flexible on the mix if the role is right.\"",
                "If pushed for one number, give the top of your range. You can come down; you can't go up."]},
            {"h": "When they ask about your current CTC", "points": [
                "Be truthful; companies check payslips. But give the full picture: fixed, variable, any due increment or bonus.",
                f"Say: \"My current CTC is {_money(total)}: {_money(fixed)} fixed and {_money(var)} variable. My expectation is based on the scope of this role and the market for it.\"",
                "Never inflate your current pay. It is the one thing that can get an offer withdrawn."]},
            {"h": "When the offer comes", "points": [
                "Thank them, show interest, and ask for the offer in writing with the full break-up. Never accept on the call.",
                "Counter once, with a reason and a specific number: \"I'm excited about this. Given the scope and the market, could we get the fixed to X?\"",
                "If fixed won't move, ask for other levers: joining bonus, a shorter path to the next band, ESOPs, notice-period buyout, a 6-month review.",
                "Use a competing offer only if it is real, and say it calmly. Bluffing breaks trust.",
                "Ask how variable pay was actually paid out last year, the probation period, and the notice period on the new side."]},
            {"h": "Notice period", "points": [
                f"Your notice period is {p.get('notice_period', '?')}. If they need you sooner, ask whether they will buy it out.",
                "Do not resign until you have the signed offer letter."]},
        ],
        "practice": ["What are your salary expectations?", "Your expectation is above our budget. Can you be flexible?",
                     "We can offer X fixed and Y variable. What do you think?", "Why should we pay you a 30% hike?"],
    }


def _intro_course():
    p = config.profile()
    try:
        r = resumes.load("A")
        role = r["experience"][0] if r["experience"] else {}
        wins = [b for b in role.get("bullets", [])][:2]
    except Exception:
        role, wins = {}, []
    yrs = p.get("total_experience_years")
    title = p.get("current_title", "")
    intro = (f"I'm {p.get('first_name', '')}, {'an' if title[:1].lower() in 'aeiou' else 'a'} {title} at {p.get('current_employer', '')} with about {yrs} years of experience. "
             + (f"Most recently I {wins[0][0].lower() + wins[0][1:].rstrip('.')}. " if wins else "")
             + (p.get("extras_line") or "") + " "
             + (p.get("reason_for_leaving") or "")).strip()
    return {
        "id": "basics", "title": "The questions every interview asks", "minutes": 15, "tag": "Basics",
        "summary": "Tell me about yourself, why you are leaving, why us, strengths, weaknesses, and the STAR method for every story.",
        "sections": [
            {"h": "Tell me about yourself (90 seconds)", "points": [
                "Present, past, future: what you do now and one result, how you got here, why this role is the next step.",
                f"A draft from your resume: \"{intro}\"",
                "End by linking to the role: \"That's why this role at [company] interests me: [one line from the JD].\""]},
            {"h": "Answer with STAR", "points": [
                "Situation (one line), Task (what you owned), Action (most of the answer, say 'I' not 'we'), Result (a number, or what changed).",
                "Keep 6 to 8 stories ready: a launch, a growth win, a failure, a conflict, working with data, influencing without authority, a tight deadline, a customer insight.",
                "Each story should take about two minutes. Put the result first if they look impatient."]},
            {"h": "Why are you leaving?", "points": [
                "Talk about what you are moving to, never about what you are escaping. Nothing negative about your manager or company.",
                f"Your version: \"{p.get('reason_for_leaving', '')}\""]},
            {"h": "Why this company?", "points": [
                "Three parts: something specific about their product or market, how your experience fits, what you want to learn there.",
                "Read their last few months of news, product launches, app reviews and the founders' or leaders' posts before the call."]},
            {"h": "Strengths and weaknesses", "points": [
                "Strength: one the JD needs, proven by a short story with a result.",
                "Weakness: a real one that does not block this role, and what you are doing about it. Not 'I'm a perfectionist'."]},
            {"h": "Questions to ask them (always ask two)", "points": [
                "What would success look like in the first 90 days?", "What is the biggest challenge the team is facing this quarter?",
                "How does this team work with product, sales and data?", "What did the last person in this role go on to do?"]},
        ],
        "practice": ["Tell me about yourself.", "Why are you looking for a change?", "Why do you want to join us?",
                     "Tell me about a time you failed.", "Tell me about a disagreement with a stakeholder.", "Where do you see yourself in three years?"],
    }


ROLE_COURSES = [
    {"id": "product_marketing", "title": "Product marketing crash course", "minutes": 20, "tag": "Role",
     "summary": "Positioning, messaging, go-to-market launches, personas, competitive work and the metrics PMMs are judged on.",
     "sections": [
         {"h": "Positioning and messaging", "points": [
             "Positioning answers: for whom, what problem, what category, why us over the alternative. Use April Dunford's frame: alternatives, unique attributes, value, best-fit customers, market category.",
             "Messaging house: one core value proposition, three pillars, proof points under each (numbers, customers, features).",
             "Test messages with sales calls, landing-page A/B tests, ad CTR and win/loss interviews."]},
         {"h": "Go-to-market launch plan", "points": [
             "Tier the launch (big, medium, small) by business impact. Not every feature deserves a press release.",
             "Plan: goal and metric, audience, message, channels, enablement (sales deck, FAQ, demo), launch day, 30/60/90-day review.",
             "Answer launch questions in this order: who is it for, what do they get, how will they hear about it, how will we know it worked."]},
         {"h": "Customers and competitors", "points": [
             "ICP (the company you sell to) vs persona (the person who uses or buys). B2B has several buyers: user, champion, budget owner.",
             "Competitive battlecards: their pitch, where they win, where we win, landmines to set, how to answer their claims.",
             "Win/loss analysis is the cheapest research you can do. Talk to five recent wins and five recent losses."]},
         {"h": "Metrics", "points": [
             "Adoption of the launched feature, activation, pipeline influenced, win rate, sales cycle length, conversion by stage, retention or expansion.",
             "Always say how you would measure it before you say what you would do."]},
     ],
     "practice": ["How would you position our product against [competitor]?", "Walk me through a launch you led, start to finish.",
                  "Adoption of a new feature is low after launch. What do you do?", "How do you work with product and sales?",
                  "Write the messaging for a new feature in two minutes."]},
    {"id": "growth_marketing", "title": "Growth marketing crash course", "minutes": 20, "tag": "Role",
     "summary": "Funnels, channels, unit economics, experiments and retention, the way growth interviews test them.",
     "sections": [
         {"h": "The funnel", "points": [
             "AARRR: acquisition, activation, retention, referral, revenue. Find the stage with the biggest drop and the biggest volume.",
             "Activation is the 'aha' moment. Define it as an action (e.g. first order within 7 days), then make more people reach it faster.",
             "Retention fixes come before acquisition spend. A leaky bucket makes every rupee of ads worse."]},
         {"h": "Unit economics", "points": [
             "CAC = spend / new customers. LTV = average order value x orders per year x margin x years retained.",
             "LTV:CAC above 3 is healthy; payback under 12 months for most consumer and SMB businesses.",
             "Blended vs paid CAC: say which one you mean."]},
         {"h": "Channels", "points": [
             "Paid (Meta, Google, affiliates), owned (SEO, content, CRM: email, push, WhatsApp), earned (referral, PR, community), partnerships.",
             "Pick channels by where the audience already is and the CAC you can afford. Scale one channel before adding the next."]},
         {"h": "Experiments", "points": [
             "Hypothesis, metric, minimum sample size, run time, decision rule written down before you start.",
             "ICE or RICE to prioritise. Run many small tests, not a few big ones. Write up losses too."]},
     ],
     "practice": ["Our sign-ups doubled but revenue is flat. Why?", "You have 10 lakh rupees for a quarter. How do you spend it?",
                  "Retention dropped 5% last month. How do you investigate?", "Tell me about an experiment that failed.",
                  "How would you grow a B2B marketplace's buyer side?"]},
    {"id": "product_management", "title": "Product management crash course", "minutes": 20, "tag": "Role",
     "summary": "Product sense, metrics, prioritisation, root-cause questions and estimation, the four PM interview rounds.",
     "sections": [
         {"h": "Product sense (design a product / improve a product)", "points": [
             "Clarify the goal, pick a user segment, list their pain points, prioritise one, brainstorm 3 solutions, pick one with trade-offs, define success metrics.",
             "Say out loud why you chose a segment and a pain point. The reasoning is what is graded."]},
         {"h": "Metrics", "points": [
             "North star metric: the one number that shows users get value (e.g. weekly orders per active buyer). Add input metrics and guardrails.",
             "For any feature: adoption, engagement, retention, business impact, and one guardrail (e.g. support tickets, cancellations)."]},
         {"h": "Root cause (metric dropped)", "points": [
             "Check data first (tracking bug? definition change?), then internal (release, pricing, outage), then external (season, competitor, regulation).",
             "Slice by platform, geography, user type, new vs returning, funnel step until the drop is isolated."]},
         {"h": "Prioritisation and execution", "points": [
             "RICE: reach x impact x confidence / effort. Or impact vs effort quadrants. Always name what you would NOT do.",
             "Talk about trade-offs with engineering and how you say no to stakeholders."]},
     ],
     "practice": ["Improve the seller onboarding experience of a B2B marketplace.", "Orders dropped 15% week on week. Why?",
                  "What is the north star metric for Swiggy Instamart?", "How do you prioritise a roadmap with three stakeholders asking for different things?",
                  "Tell me about a product decision you made with incomplete data."]},
    {"id": "marketplace_category", "title": "Category and marketplace crash course", "minutes": 18, "tag": "Role",
     "summary": "Category P&L, assortment, pricing, seller growth and marketplace health metrics.",
     "sections": [
         {"h": "Category P&L", "points": [
             "GMV = traffic x conversion x average order value. Net revenue = GMV x take rate (commission, ads, fees) minus discounts.",
             "Contribution margin after logistics, payment, discounts and returns. Know which lever you would pull and what it costs."]},
         {"h": "Assortment and pricing", "points": [
             "Cover the head (top-selling SKUs) first, then the long tail. Track availability, price competitiveness vs Amazon/Flipkart and selection gaps from search.",
             "Price benchmarking, deal calendars, bank offers, and how discounts change margin and repeat rate."]},
         {"h": "Seller side", "points": [
             "Acquire, activate (first listing, first order), grow (ads, catalogue quality), retain. Seller NPS and cancellations matter.",
             "A marketplace has two sides: say which side is the constraint right now and fix that one first."]},
         {"h": "Metrics", "points": [
             "GMV, orders, AOV, conversion, take rate, active sellers, fill rate, cancellations, returns, repeat buyers, contribution margin."]},
     ],
     "practice": ["GMV in your category fell 10% this month. Walk me through it.", "How would you grow a new category from zero?",
                  "Should we run a 30% discount sale next month?", "How do you get more sellers to list on our platform?"]},
    {"id": "case", "title": "Case questions and guesstimates", "minutes": 15, "tag": "Skill",
     "summary": "How to structure business cases and market-size estimates the way Indian product and growth interviews ask them.",
     "sections": [
         {"h": "Any case question", "points": [
             "Repeat the question, ask 2-3 clarifying questions (goal, timeline, constraints), then share a structure before solving.",
             "Think out loud. Round numbers. Summarise the answer and the next step at the end."]},
         {"h": "Guesstimates", "points": [
             "Top down: India population ~140 crore -> urban ~35% -> internet users -> your segment. Or bottom up: stores x orders per store.",
             "Write each assumption, sanity-check the final number against something you know.",
             "Example: 'Online grocery orders per day in Bengaluru' = households x share ordering online x orders per week / 7."]},
         {"h": "Profitability and growth cases", "points": [
             "Profit = revenue - cost. Revenue = volume x price; cost = fixed + variable. Find which piece changed.",
             "Growth: new users, more usage per user, higher price, new markets, new products. Pick by size and effort."]},
     ],
     "practice": ["Estimate the number of D2C brands selling online in India.", "A quick-commerce app's profit fell 20%. Why?",
                  "Should a B2B marketplace launch in Tier-2 cities?"]},
    {"id": "remote_abroad", "title": "Interviewing for remote jobs abroad", "minutes": 10, "tag": "Remote",
     "summary": "What US, UK and APAC companies hiring remotely in India look for, how they pay, and what to check.",
     "sections": [
         {"h": "What they look for", "points": [
             "Clear written English, async habits (written updates, docs, recorded demos), ownership without supervision, overlap with their working hours.",
             "Expect a take-home task or a written exercise. Treat it like the real job: crisp, structured, on time."]},
         {"h": "How you get paid", "points": [
             "Usually as a contractor (you invoice, they pay in USD/GBP), or through an employer-of-record such as Deel or Remote.com that employs you in India.",
             "Ask: contractor or employee, currency, payment platform, paid leave, equipment, and who pays tax and fees.",
             "Contractor income is taxed as professional income in India; talk to a CA before you sign."]},
         {"h": "Salary talk", "points": [
             "Many companies pay location-adjusted rates. Research the role's pay in India and in their home market; aim between the two.",
             "Quote annual figures in their currency and say whether it is gross."]},
         {"h": "Red flags", "points": [
             "Asking you to pay for training or equipment, interviews only over chat, an offer without a video call, or a company with no web presence."]},
     ],
     "practice": ["Why do you want to work remotely?", "How do you keep a remote team updated on your work?",
                  "Tell me about a project you drove without much supervision.", "What overlap with our time zone can you give?"]},
]

FUNCTION_COURSE = {"product_marketing": "product_marketing", "growth_marketing": "growth_marketing", "product_management": "product_management",
                   "ai_product": "product_management", "marketplace_category": "marketplace_category",
                   "ecommerce_commercial": "marketplace_category", "tech_business": "case"}


def courses():
    return [_salary_course(), _intro_course(), *ROLE_COURSES]


def course(cid):
    return next((c for c in courses() if c["id"] == cid), None)


# ------------------------------------------------------------------ per-job brief

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "about_company": {"type": "string"},
        "what_they_want": {"type": "array", "items": {"type": "string"}},
        "your_best_stories": {"type": "array", "items": {"type": "object", "properties": {
            "for": {"type": "string"}, "story": {"type": "string"}}, "required": ["for", "story"], "additionalProperties": False}},
        "gaps": {"type": "array", "items": {"type": "object", "properties": {
            "gap": {"type": "string"}, "how_to_handle": {"type": "string"}}, "required": ["gap", "how_to_handle"], "additionalProperties": False}},
        "likely_questions": {"type": "array", "items": {"type": "string"}},
        "questions_to_ask": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["about_company", "what_they_want", "your_best_stories", "gaps", "likely_questions", "questions_to_ask"],
    "additionalProperties": False,
}

BRIEF_SYSTEM = ("You are an interview coach for one candidate in India. Use only facts from the RESUME for the candidate's "
                "experience; never invent results or numbers. Be specific to this job description. Plain English, no em dashes.")


def _brief_file(job_id):
    return config.DATA_DIR / f"prep_{re.sub(r'[^A-Za-z0-9_-]', '', job_id)}.json"


def job_brief(job_id, refresh=False):
    f = _brief_file(job_id)
    if f.exists() and not refresh:
        return json.loads(f.read_text(encoding="utf-8"))
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not job:
        return None
    key = job.get("resume_key") or "A"
    res = resumes.load(key)
    jd = (job.get("description") or "")[:9000]
    prompt = (f"JOB: {job['title']} at {job['company']} ({job.get('city') or ''})\n\nJOB DESCRIPTION:\n{jd}\n\n"
              f"RESUME:\n{res['full_text'][:8000]}\n\n"
              "Write an interview prep brief: what the company does (from the JD, one or two sentences; say if unclear), the 4-6 things "
              "they most want, 3-5 resume stories to tell and which requirement each answers, the gaps between the JD and the resume "
              "with an honest way to handle each, 8-10 likely interview questions for this exact role, and 4 sharp questions to ask them.")
    data = llm.structured(BRIEF_SYSTEM, prompt, BRIEF_SCHEMA, max_tokens=6000, effort="medium")
    if data:
        data["generator"] = "claude"
    else:
        jd_skills = skills_in(f"{job['title']} {jd}")
        have = set(res["skills_detected"])
        c = course(FUNCTION_COURSE.get(job.get("function"), "basics")) or _intro_course()
        data = {
            "about_company": f"{job['company']}: read their website, recent news and product before the call.",
            "what_they_want": sorted(jd_skills)[:8] or ["Read the JD and list the 5 requirements it repeats most."],
            "your_best_stories": [{"for": "Relevant experience", "story": a["bullet"]} for a in resumes.achievements(key, job, n=4)],
            "gaps": [{"gap": g, "how_to_handle": "Say honestly what you have done that is closest, and how you would pick it up."}
                     for g in sorted(jd_skills - have)[:5]],
            "likely_questions": c["practice"] + ["Tell me about yourself.", f"Why {job['company']}?"],
            "questions_to_ask": ["What would success look like in the first 90 days?", "What is the team's biggest challenge this quarter?",
                                 "How does this role work with product and sales?", "What does the interview process look like from here?"],
            "generator": "template",
        }
    data["job"] = {"id": job_id, "title": job["title"], "company": job["company"]}
    data["course"] = FUNCTION_COURSE.get(job.get("function"), "basics")
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data
