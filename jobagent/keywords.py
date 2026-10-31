"""Role taxonomy and skill vocabulary used by scoring, resume selection and tailoring."""
import re

# Target functions. `strong` title patterns score full function-fit, `weak` ones partial.
FUNCTIONS = {
    "product_marketing": {
        "label": "Product Marketing",
        "strong": [r"product marketing", r"\bpmm\b", r"go[- ]to[- ]market", r"\bgtm\b"],
        "weak": [r"marketing manager", r"brand manager", r"marketing lead", r"marketing strategy"],
    },
    "growth_marketing": {
        "label": "Growth Marketing",
        "strong": [r"growth market", r"growth manager", r"growth lead", r"performance market",
                   r"user acquisition", r"lifecycle market", r"retention market", r"digital marketing manager"],
        "weak": [r"\bgrowth\b", r"digital marketing", r"crm manager", r"acquisition"],
    },
    "product_management": {
        "label": "Product Management",
        "strong": [r"product manager", r"\bapm\b", r"product owner", r"growth product"],
        "weak": [r"product analyst", r"product lead", r"product specialist"],
    },
    "marketplace_category": {
        "label": "Marketplace & Category",
        "strong": [r"category manag", r"category lead", r"marketplace", r"seller (growth|success|acquisition|onboarding)",
                   r"assortment", r"merchandi[sz]", r"supply growth", r"vendor manag"],
        "weak": [r"\bcategory\b", r"\bseller\b", r"catalog", r"buyer\b"],
    },
    "ecommerce_commercial": {
        "label": "E-commerce & Commercial",
        "strong": [r"e-?commerce", r"commercial strategy", r"quick commerce", r"\bd2c\b", r"business strategy",
                   r"strategy (and|&) operations", r"biz ?ops", r"business operations", r"commercial manager"],
        "weak": [r"\bstrategy\b", r"\bcommercial\b", r"business manager", r"revenue"],
    },
    "ai_product": {
        "label": "AI Product & Growth",
        "strong": [r"\bai product", r"gen ?ai product", r"product manager.{0,20}\b(ai|ml|genai|llm)\b",
                   r"\b(ai|genai|llm)\b.{0,20}product manager", r"\bai growth", r"\bai strategy"],
        "weak": [r"\b(ai|genai|llm)\b"],
    },
    "tech_business": {
        "label": "Technology Business",
        "strong": [r"partnerships? manager", r"business development manager.{0,20}(saas|tech|platform)",
                   r"solutions? marketing", r"platform strategy"],
        "weak": [r"program manager", r"business analyst", r"strategy manager", r"partnerships?"],
    },
}

# Titles that are never a fit for this candidate.
EXCLUDE_TITLE = [
    r"engineer", r"developer", r"\bsde\b", r"scientist", r"architect", r"devops", r"\bqa\b", r"tester",
    r"accountant", r"\bfinance\b", r"legal", r"counsel", r"paralegal", r"payroll", r"recruit", r"talent acquisition",
    r"\bhr\b", r"human resources", r"nurse", r"driver", r"warehouse", r"technician", r"\bintern\b", r"internship",
    r"graphic designer", r"ux designer", r"ui designer", r"video editor", r"copywriter", r"customer support",
    r"customer service", r"call cent", r"telecaller", r"field sales", r"sales executive", r"account executive",
    r"inside sales", r"tele ?sales", r"security", r"compliance", r"\baudit", r"tax\b", r"trainee",
    r"technical (program|project|consult|account|support|writer|solutions?)", r"solutions? (architect|engineer|consultant)",
    r"\bimplementation\b", r"\bpre-?sales\b",
]

# Seniority well beyond 4.4 years of experience.
TOO_SENIOR = [r"\bdirector\b", r"\bvp\b", r"vice president", r"\bchief\b", r"\bcxo\b", r"\bhead of\b",
              r"\bhead\b", r"principal", r"principle", r"\bstaff\b", r"general manager", r"\bavp\b",
              r"group product manager", r"\bgpm\b"]

# Titles usually below the candidate's level (current CTC 19 LPA): the score is capped, not excluded.
TOO_JUNIOR = [r"\bops associate\b", r"\boperations associate\b", r"catalog(ue)? (specialist|associate|executive)",
              r"\bspecialist\s*(1|i)\b", r"\bsupport\b", r"\bcoordinator\b", r"\bjunior\b", r"\bjr\.?\s",
              r"\bexecutive\b(?!\s*(director|vice|officer|search))", r"\bassociate\s*$", r"\bassociate\s*[-–,]",
              r"^(sr\.?\s+|senior\s+)?associate\b(?!\s+(product manager|director|vice))",
              r"^(?!.*\b(senior|sr|lead|manager|head)\b).*\bspecialist\b",
              r"^(?!.*\b(senior|sr|lead|manager)\b).*\banalyst\b"]

# Senior individual-contributor titles that usually expect 5-8 years even when the JD omits it.
SENIOR_IC = [r"\bsenior\b", r"\bsr\.?\s", r"\blead\b", r"\bmanager ii\b"]

# Skill vocabulary: canonical name -> synonyms (regex). Candidate coverage is computed from resume text,
# so nothing here is claimed for the candidate unless it appears in a resume.
SKILLS = {
    "Go-to-market strategy": [r"go[- ]to[- ]market", r"\bgtm\b"],
    "Product launches": [r"\blaunch(es|ing)?\b"],
    "Positioning & messaging": [r"positioning", r"messaging"],
    "Funnel analysis": [r"funnel"],
    "Conversion optimization": [r"conversion", r"\bcro\b"],
    "KPI trees / metrics": [r"\bkpi", r"metrics?\b", r"north star"],
    "Cohort analysis": [r"cohort"],
    "A/B testing & experimentation": [r"a/b test", r"experiment"],
    "User acquisition": [r"acquisition"],
    "User engagement & retention": [r"engagement", r"retention"],
    "CAC optimization": [r"\bcac\b", r"customer acquisition cost"],
    "Performance marketing": [r"performance marketing", r"paid (media|marketing|acquisition)", r"\bppc\b", r"sem\b"],
    "Google Ads": [r"google ads", r"adwords", r"google and meta ad"],
    "Meta Ads": [r"meta ads", r"facebook ads", r"meta ad", r"google and meta"],
    "Google Shopping / Performance Max": [r"google shopping", r"performance max", r"\bpmax\b"],
    "SEO": [r"\bseo\b", r"search engine optimi"],
    "GEO / LLM search visibility": [r"\bgeo\b", r"llm search", r"generative engine"],
    "Email marketing": [r"email marketing", r"\bemail\b"],
    "WhatsApp marketing": [r"whatsapp"],
    "CRM / lifecycle marketing": [r"\bcrm\b", r"lifecycle"],
    "SQL": [r"\bsql\b"],
    "Tableau": [r"tableau"],
    "Dashboards & reporting": [r"dashboard", r"reporting"],
    "Data analysis": [r"data[- ]driven", r"analytics", r"data analysis", r"data-first"],
    "Excel / Microsoft Office": [r"\bexcel\b", r"microsoft office", r"spreadsheets?"],
    "PRDs": [r"\bprds?\b", r"product requirement"],
    "Roadmapping & prioritization": [r"prioriti[sz]", r"roadmap"],
    "User research": [r"user research", r"customer research", r"user interviews?"],
    "User journey mapping": [r"user journey", r"customer journey"],
    "Figma / prototyping": [r"figma", r"prototyp", r"wireframe"],
    "Design thinking": [r"design thinking"],
    "Cross-functional collaboration": [r"cross[- ]functional", r"cross[- ]team", r"stakeholder"],
    "B2B e-commerce / marketplace": [r"\bb2b\b", r"marketplace"],
    "E-commerce": [r"e-?commerce"],
    "Seller onboarding & activation": [r"seller onboarding", r"onboard(ed|ing)? .{0,20}sellers?", r"seller activation", r"activated .{0,10}sellers"],
    "Seller / catalogue management": [r"catalog(ue)?", r"listing (quality|optimi)", r"seller manag"],
    "Pricing & competitor benchmarking": [r"pricing", r"competitor", r"benchmark"],
    "Assortment & category expansion": [r"assortment", r"category expansion", r"category growth", r"category management"],
    "GMV / sales target ownership": [r"\bgmv\b", r"sales target", r"revenue target"],
    "Average order value": [r"average order value", r"\baov\b"],
    "Vendor & logistics management": [r"vendor", r"logistic"],
    "Market expansion": [r"new target markets", r"market expansion", r"new markets"],
    "Web scraping": [r"scrap(er|ing)"],
    "Gen AI / AI tools": [r"gen ?ai", r"\bai tools\b", r"\bllms?\b", r"claude", r"lovable", r"n8n", r"codex"],
    "Workflow automation": [r"n8n", r"automation"],
    "Team leadership": [r"lead a team", r"team management", r"people management", r"manag(e|ing) a team"],
    "Lead qualification": [r"lead qualification", r"lead gen"],
    "Event marketing": [r"\bevents?\b"],
    # Common JD skills that are NOT in the resumes; kept so gaps are reported honestly.
    "Python": [r"\bpython\b"],
    "P&L ownership": [r"p&l", r"profit and loss"],
    "Brand marketing": [r"brand (marketing|strategy|building)"],
    "Content marketing": [r"content (marketing|strategy)"],
    "Social media marketing": [r"social media"],
    "Product analytics tools": [r"mixpanel", r"amplitude", r"google analytics", r"\bga4\b", r"clevertap", r"moengage"],
    "Sales enablement": [r"sales enablement", r"battle ?cards"],
    "Pricing & packaging (SaaS)": [r"packaging"],
    "SaaS": [r"\bsaas\b"],
    "Fintech / lending": [r"fintech", r"lending", r"\bnbfc\b", r"payments"],
    "Supply chain": [r"supply chain"],
    "Account management": [r"account management", r"key accounts?"],
    "Machine learning": [r"machine learning", r"\bml\b"],
}

_COMPILED = {name: [re.compile(p, re.I) for p in pats] for name, pats in SKILLS.items()}


def skills_in(text):
    """Canonical skill names mentioned in text."""
    text = text or ""
    return {name for name, pats in _COMPILED.items() if any(p.search(text) for p in pats)}


def classify_title(title):
    """Return (function_key, strength) where strength is 'strong' | 'weak' | None."""
    t = (title or "").lower()
    best = (None, None)
    for key, spec in FUNCTIONS.items():
        if any(re.search(p, t) for p in spec["strong"]):
            # AI product beats generic product management when both match
            if best[1] != "strong" or key == "ai_product":
                best = (key, "strong")
        elif best[0] is None and any(re.search(p, t) for p in spec["weak"]):
            best = (key, "weak")
    return best


def classify_job(title, description):
    """Like classify_title, but a loose title ('Brand Manager') takes its role family from the JD."""
    fn, strength = classify_title(title)
    if strength == "strong":
        return fn, strength
    text = (description or "").lower()
    hits = {k: sum(len(re.findall(p, text)) for p in spec["strong"]) for k, spec in FUNCTIONS.items()}
    top = max(hits, key=hits.get)
    if hits[top] >= 3 and hits[top] > hits.get(fn, 0):
        return top, strength or "description"
    return fn, strength


def is_excluded_title(title):
    t = (title or "").lower()
    if any(re.search(p, t) for p in EXCLUDE_TITLE):
        # "product marketing engineer"-style hybrids are still excluded; "growth product manager" is not
        return True
    return False


def is_too_senior(title):
    t = (title or "").lower()
    return any(re.search(p, t) for p in TOO_SENIOR)


def is_too_junior(title):
    t = (title or "").lower()
    return any(re.search(p, t) for p in TOO_JUNIOR)


def is_senior_ic(title):
    t = (title or "").lower()
    return any(re.search(p, t) for p in SENIOR_IC)


def is_relevant_title(title):
    if is_excluded_title(title):
        return False
    fn, strength = classify_title(title)
    return fn is not None
