"""Recruiter / hiring-manager discovery from public, professional sources.

Sources, in order of trust:
  1. Emails printed in the job description itself.
  2. Hunter.io domain search (public web sources, with confidence + verification), if HUNTER_API_KEY is set.
  3. Role addresses (careers@, talent@ ...) published on the company's own careers/contact pages.
  4. Pattern guesses for a named person, only when the domain's pattern is known; marked unverified.
LinkedIn profiles are never scraped. We build people-search links the user opens, and the user pastes back
the profile URL they choose.
"""
import re
from urllib.parse import quote_plus

from . import config, db
from .keywords import FUNCTIONS
from .sources.common import session

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ROLE_LOCAL = re.compile(r"^(careers?|jobs?|talent|hr|hiring|recruit\w*|people|ta|campus)$", re.I)
RECRUITER_TITLES = re.compile(r"recruit|talent|\bhr\b|human resources|people (partner|operations)|hiring|\bta\b", re.I)
FUNCTION_TITLES = {
    "product_marketing": r"product marketing|marketing|gtm|go-to-market",
    "growth_marketing": r"growth|marketing|acquisition|performance",
    "product_management": r"product",
    "marketplace_category": r"category|marketplace|seller|merchandis|supply",
    "ecommerce_commercial": r"e-?commerce|commercial|strategy|business",
    "ai_product": r"product|\bai\b|ml",
    "tech_business": r"strategy|business|partnership|program",
}
SENIOR = re.compile(r"head|director|vp|vice president|lead|chief|general manager", re.I)


def _save(job_id, company, c):
    exists = db.one("SELECT id FROM contacts WHERE job_id = ? AND ((email IS NOT NULL AND email = ?) OR (name = ? AND name IS NOT NULL))",
                    (job_id, c.get("email"), c.get("name")))
    if exists:
        db.update("contacts", "id", exists["id"], {k: v for k, v in c.items() if v})
        return exists["id"]
    return db.insert("contacts", {"job_id": job_id, "company": company, "created_at": db.now(), **c})


def role_type(title, function):
    t = title or ""
    if RECRUITER_TITLES.search(t):
        return "recruiter"
    if re.search(FUNCTION_TITLES.get(function, "product|marketing"), t, re.I):
        return "department_head" if SENIOR.search(t) else "hiring_manager"
    return "other"


def from_description(job):
    out = []
    domain = (job.get("company_domain") or "").lower()
    for email in set(EMAIL_RE.findall(job.get("description") or "")):
        email = email.strip(".").lower()
        if email.endswith((".png", ".jpg")):
            continue
        out.append({"email": email, "name": None, "title": "Listed in job posting", "role_type": "recruiter",
                    "email_status": "public", "email_confidence": 90 if domain and email.endswith(domain) else 60,
                    "source": "job description", "source_url": job.get("url")})
    return out


def hunter(domain, function):
    key = config.secret("HUNTER_API_KEY")
    if not key or not domain:
        return [], None
    r = session.get("https://api.hunter.io/v2/domain-search",
                    params={"domain": domain, "api_key": key, "limit": 50, "type": "personal"}, timeout=25)
    if r.status_code != 200:
        return [], f"Hunter returned HTTP {r.status_code}: {r.text[:120]}"
    data = r.json().get("data") or {}
    out = []
    for e in data.get("emails") or []:
        title = e.get("position") or ""
        rt = role_type(title, function)
        if rt == "other":
            continue
        ver = (e.get("verification") or {}).get("status")
        out.append({
            "name": " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x) or None,
            "title": title, "role_type": rt, "email": e.get("value"),
            "email_status": "verified" if ver == "valid" else "public", "email_confidence": e.get("confidence"),
            "linkedin_url": e.get("linkedin"), "source": "hunter.io",
            "source_url": ((e.get("sources") or [{}])[0]).get("uri"),
        })
    return out, data.get("pattern")


APOLLO = "https://api.apollo.io/api/v1"
RECRUITER_TITLES_Q = ["Talent Acquisition", "Technical Recruiter", "Recruiter", "HR Business Partner", "Talent Partner"]
FUNCTION_TITLES_Q = {
    "product_marketing": ["Head of Product Marketing", "Director of Marketing", "Product Marketing Manager", "VP Marketing"],
    "growth_marketing": ["Head of Growth", "Growth Marketing Manager", "Director of Marketing", "VP Marketing"],
    "product_management": ["Head of Product", "Director of Product", "Group Product Manager", "VP Product"],
    "marketplace_category": ["Category Head", "Head of Category", "Category Manager", "Head of Marketplace"],
    "ecommerce_commercial": ["Head of E-commerce", "Head of Growth", "Director of Commercial", "Business Head"],
    "ai_product": ["Head of Product", "Director of Product", "Head of AI"],
    "tech_business": ["Head of Strategy", "Director of Business Development", "Head of Partnerships"],
}


def _apollo(path, body=None, params=None):
    r = session.post(f"{APOLLO}{path}", json=body or {}, params=params or {}, timeout=40,
                     headers={"x-api-key": config.secret("APOLLO_API_KEY"), "Cache-Control": "no-cache",
                              "Content-Type": "application/json", "accept": "application/json"})
    if r.status_code in (401, 403):
        raise RuntimeError("Apollo rejected the key (a 'master' API key is needed for people search): " + r.text[:120])
    if r.status_code == 429:
        raise RuntimeError("Apollo rate limit hit; try again in a minute")
    r.raise_for_status()
    return r.json()


def apollo(job, domain, max_reveals=None):
    """Free people search, then reveal emails (1 credit each) for the best few. Returns (contacts, note)."""
    if not config.secret("APOLLO_API_KEY"):
        return [], "Apollo not configured"
    cap = max_reveals if max_reveals is not None else int(config.settings().get("apollo_max_reveals_per_job", 3))
    company = job["company"]
    query = {"person_titles": RECRUITER_TITLES_Q + FUNCTION_TITLES_Q.get(job.get("function"), []),
             "person_locations": ["India"], "per_page": 25, "page": 1}
    if domain:
        query["q_organization_domains_list"] = [domain]
    else:
        query["q_keywords"] = company
    found = _apollo("/mixed_people/api_search", query).get("people") or []

    def belongs(p):
        org = ((p.get("organization") or {}).get("name") or "").lower()
        return bool(domain) or not org or company.lower().split()[0] in org

    cands = []
    for p in found:
        title = p.get("title") or ""
        rt = role_type(title, job.get("function"))
        if rt == "other" or not belongs(p) or p.get("has_email") is False:
            continue
        # recruiters first, then the people closest to the hiring team
        cands.append((0 if rt == "recruiter" else 1 if rt == "hiring_manager" else 2, p, rt))
    cands.sort(key=lambda c: c[0])
    out = []
    for _, p, rt in cands[:cap]:
        m = (_apollo("/people/match", {"id": p.get("id")}, {"reveal_personal_emails": "false", "reveal_phone_number": "false"})
             .get("person") or {})
        email = m.get("email")
        if not email or "email_not_unlocked" in email:
            continue
        out.append({"name": m.get("name") or " ".join(x for x in (m.get("first_name"), m.get("last_name")) if x) or None,
                    "title": m.get("title") or p.get("title"), "role_type": rt, "email": email,
                    "email_status": "verified" if m.get("email_status") == "verified" else "public",
                    "email_confidence": 95 if m.get("email_status") == "verified" else 70,
                    "linkedin_url": m.get("linkedin_url"), "source": "apollo.io", "source_url": None})
    return out, f"Apollo: {len(found)} people found, {len(cands)} relevant, {len(out)} emails revealed (cap {cap} credits)"


def careers_page_emails(domain):
    """Role mailboxes published on the company's own site."""
    out = []
    if not domain:
        return out
    for path in ("/careers", "/jobs", "/contact", "/contact-us", "/about"):
        try:
            r = session.get(f"https://{domain}{path}", timeout=12)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        for email in set(EMAIL_RE.findall(r.text)):
            email = email.lower()
            local, _, host = email.partition("@")
            if host.endswith(domain) and ROLE_LOCAL.match(local):
                out.append({"email": email, "name": None, "title": f"{local}@ mailbox", "role_type": "recruiter",
                            "email_status": "public", "email_confidence": 80, "source": "company website",
                            "source_url": r.url})
    return out


PATTERNS = {
    "{first}.{last}": lambda f, l: f"{f}.{l}", "{first}{last}": lambda f, l: f"{f}{l}", "{first}": lambda f, l: f,
    "{f}{last}": lambda f, l: f"{f[0]}{l}", "{first}_{last}": lambda f, l: f"{f}_{l}", "{first}{l}": lambda f, l: f"{f}{l[0]}",
    "{last}.{first}": lambda f, l: f"{l}.{f}", "{f}.{last}": lambda f, l: f"{f[0]}.{l}",
}


def infer_pattern(address, name=None):
    """Which format an email follows. With the person's name it is exact; from the address alone it is a best guess."""
    local = address.split("@")[0].lower()
    parts = [p for p in re.sub(r"[^a-z ]", "", (name or "").lower()).split() if p]
    if len(parts) >= 2:
        for key, fn in PATTERNS.items():
            if fn(parts[0], parts[-1]) == local:
                return key
    if "." in local:
        a, b = local.split(".", 1)
        return "{f}.{last}" if len(a) == 1 else "{first}.{last}" if b else None
    if "_" in local:
        return "{first}_{last}"
    return None


def learn_pattern(address, name=None):
    """Remember a known-real address for its company, so guesses for the same company follow its real format."""
    domain = base_domain(address.rsplit("@", 1)[-1])
    pattern = infer_pattern(address, name)
    existing = db.one("SELECT * FROM email_patterns WHERE domain = ?", (domain,))
    if existing:
        db.update("email_patterns", "domain", domain, {"pattern": pattern or existing["pattern"], "example": address, "confirmed": 1})
    else:
        db.insert("email_patterns", {"domain": domain, "pattern": pattern, "example": address, "confirmed": 1, "bounces": 0, "created_at": db.now()})
    return {"domain": domain, "pattern": pattern, "example": address}


def name_from_address(address):
    local = re.sub(r"[0-9]+", "", address.split("@")[0])
    parts = [p for p in re.split(r"[._\-]+", local) if p]
    return " ".join(p.capitalize() for p in parts) if len(parts) >= 2 else None


def email_trust(address, contact=None):
    """Cross-check an address before it is approved or sent. Returns (ok, reason)."""
    domain = base_domain(address.rsplit("@", 1)[-1])
    if db.one("SELECT 1 FROM contacts WHERE lower(email) = ? AND email_status = 'bounced'", (address.lower(),)):
        return False, "This address already bounced."
    if not domain_accepts_mail(domain):
        return False, f"{domain} has no working mail server."
    status = (contact or {}).get("email_status") or (db.one("SELECT email_status FROM contacts WHERE lower(email) = ? LIMIT 1", (address.lower(),)) or {}).get("email_status")
    if status in ("public", "verified"):
        return True, "Found by you or published by the company."
    row = db.one("SELECT * FROM email_patterns WHERE domain = ?", (domain,))
    if row and row["confirmed"]:
        return True, f"Follows {domain}'s confirmed format ({row['pattern']}), e.g. {row['example']}."
    tried = tried_patterns(domain)
    if len(tried) >= 4:
        return False, f"Tried {len(tried)} email formats at {domain} and all bounced. Add a real address from that company (Sheet tab > I found a real email address)."
    return True, "Guessed from a common format; not verified. Bounces are tracked and the next guess uses another format."


PATTERN_ORDER = ["{first}.{last}", "{first}{last}", "{f}{last}", "{first}", "{first}_{last}", "{f}.{last}", "{first}{l}", "{last}.{first}"]


def tried_patterns(domain):
    import json
    row = db.one("SELECT tried FROM email_patterns WHERE domain = ?", (base_domain(domain),))
    try:
        return json.loads(row["tried"]) if row and row.get("tried") else []
    except ValueError:
        return []


def record_bounced_format(address, name=None):
    """A guessed address bounced: remember that format failed for this company, so the next guess uses another."""
    import json
    domain = base_domain(address.rsplit("@", 1)[-1])
    fmt = infer_pattern(address, name)
    tried = tried_patterns(domain)
    if fmt and fmt not in tried:
        tried.append(fmt)
    row = db.one("SELECT 1 FROM email_patterns WHERE domain = ?", (domain,))
    if row:
        db.execute("UPDATE email_patterns SET tried = ?, bounces = bounces + 1 WHERE domain = ?", (json.dumps(tried), domain))
    else:
        db.insert("email_patterns", {"domain": domain, "pattern": None, "example": None, "confirmed": 0, "bounces": 1,
                                     "tried": json.dumps(tried), "created_at": db.now()})


def guess_email(name, domain, pattern=None):
    parts = [p for p in re.sub(r"[^a-z ]", "", (name or "").lower()).split() if p]
    if len(parts) < 2 or not domain:
        return None, None
    f, l = parts[0], parts[-1]
    if not pattern:
        row = db.one("SELECT pattern, confirmed FROM email_patterns WHERE domain = ?", (base_domain(domain),))
        if row and row["confirmed"] and row["pattern"]:
            pattern = row["pattern"]                       # a real address from this company showed its format
        else:
            tried = tried_patterns(domain)
            pattern = next((p for p in PATTERN_ORDER if p not in tried), PATTERN_ORDER[0])
    if pattern in PATTERNS:
        return f"{PATTERNS[pattern](f, l)}@{domain}", "pattern_guess"
    return f"{f}.{l}@{domain}", "pattern_guess"


def base_domain(domain):
    """careers.emeritus.org -> emeritus.org (mail is on the main domain, not on career-site subdomains)."""
    d = re.sub(r"^(www|careers?|jobs?|apply|hire|hiring|talent|work)\.", "", (domain or "").lower().strip().split("/")[0])
    return d


def guess_domain(company):
    """Find a company's website by trying likely domains and checking the page really mentions the company."""
    words = re.sub(r"\b(private|pvt|limited|ltd|inc|llp|technologies|solutions|services|india)\b|[^a-z0-9 ]", " ", company.lower()).split()
    if not words:
        return None
    slugs = list(dict.fromkeys(["".join(words), words[0]]))
    for slug in slugs:
        for tld in (".com", ".in", ".co.in", ".org", ".io", ".co", ".ai"):
            domain = slug + tld
            try:
                r = session.get(f"https://{domain}", timeout=8, allow_redirects=True)
            except Exception:
                continue
            text = re.sub(r"[^a-z0-9 ]", " ", r.text[:80000].lower())
            if r.ok and all(w in text for w in words[:3]):          # every word of the name, so 'kyros.com' is not taken for 'Kyros Solution'
                return re.sub(r"^www\.", "", r.url.split("/")[2].lower()).split(":")[0]
    return None


def domain_accepts_mail(domain):
    try:
        import dns.resolver
        return bool(dns.resolver.resolve(domain, "MX", lifetime=8))
    except Exception:
        return False


def linkedin_search_links(job):
    company = job["company"]
    fn_words = {"product_marketing": "product marketing", "growth_marketing": "growth marketing",
                "product_management": "product", "marketplace_category": "category", "ecommerce_commercial": "ecommerce",
                "ai_product": "AI product", "tech_business": "strategy"}.get(job.get("function"), "marketing")
    city = job.get("city") if job.get("city") not in (None, "Remote") else "India"
    q = lambda s: quote_plus(s)
    return [
        {"label": f"Recruiters at {company}",
         "url": f"https://www.linkedin.com/search/results/people/?keywords={q(f'{company} talent acquisition recruiter')}"},
        {"label": f"{fn_words.title()} leaders at {company}",
         "url": f"https://www.linkedin.com/search/results/people/?keywords={q(f'{company} {fn_words} head manager')}"},
        {"label": "Google: recruiter profiles",
         "url": f"https://www.google.com/search?q={q(f'site:linkedin.com/in {company} (recruiter OR talent acquisition) {city}')}"},
        {"label": "Google: hiring manager profiles",
         "url": f"https://www.google.com/search?q={q(f'site:linkedin.com/in {company} {fn_words} (head OR lead OR manager) {city}')}"},
    ]


def discover(job_id):
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    domain = (job.get("company_domain") or "").lower() or None
    if not domain:
        for c in config.companies():
            if c["name"].lower() == job["company"].lower():
                domain = c.get("domain")
    notes = []
    found = from_description(job)
    pattern = None
    if config.integrations()["hunter"]:
        h, pattern = hunter(domain, job.get("function"))
        if isinstance(pattern, str) and pattern.startswith("Hunter returned"):
            notes.append(pattern)
            pattern = None
        found += h
        notes.append(f"Hunter: {len(h)} relevant people at {domain}" + (f", pattern {pattern}" if pattern else ""))
    else:
        notes.append("Hunter.io not configured; named recruiter emails need HUNTER_API_KEY or manual entry")
    if config.integrations()["apollo"]:
        try:
            a, note = apollo(job, domain)
            found += a
            notes.append(note)
        except Exception as e:
            notes.append(f"Apollo error: {str(e)[:140]}")
    else:
        notes.append("Apollo not configured (APOLLO_API_KEY)")
    site = careers_page_emails(domain)
    found += site
    notes.append(f"Company website: {len(site)} role mailboxes")
    if domain:
        notes.append(f"{domain} {'accepts' if domain_accepts_mail(domain) else 'has no MX record for'} email")
    ids = [_save(job_id, job["company"], c) for c in found]
    db.log_event(job_id, "contacts_discovered", {"count": len(ids), "notes": notes})
    return {"contacts": contacts_for(job_id), "notes": notes, "pattern": pattern, "domain": domain,
            "linkedin_search": linkedin_search_links(job)}


def add_manual(job_id, name, title, email=None, linkedin_url=None, pattern=None):
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    status = "public" if email else None
    domain = job.get("company_domain")
    if not email and name and domain:
        email, status = guess_email(name, domain, pattern)
    cid = _save(job_id, job["company"], {"name": name, "title": title, "role_type": role_type(title, job.get("function")),
                                          "email": email, "email_status": status or "unknown",
                                          "email_confidence": 30 if status == "pattern_guess" else None,
                                          "linkedin_url": linkedin_url, "source": "manual"})
    return db.one("SELECT * FROM contacts WHERE id = ?", (cid,))


def contacts_for(job_id):
    order = "CASE role_type WHEN 'recruiter' THEN 0 WHEN 'hiring_manager' THEN 1 WHEN 'department_head' THEN 2 ELSE 3 END"
    return db.query(f"SELECT * FROM contacts WHERE job_id = ? ORDER BY {order}, email_confidence DESC", (job_id,))

def verify_email(address, probe_from=None):
    """Ask the recipient's mail server whether the mailbox exists (SMTP RCPT check, nothing is sent).
    Returns {"status": "valid" | "invalid" | "unknown", "detail": str}.
      valid   - the server accepted this address and rejected a made-up one on the same domain
      invalid - the server said the mailbox does not exist
      unknown - could not tell: port blocked, server accepts any address, or it refused to answer
    """
    import random
    import smtplib
    import socket
    domain = address.rsplit("@", 1)[-1].lower()
    probe_from = probe_from or config.secret("SMTP_USER") or "check@example.com"
    try:
        import dns.resolver
        mx = sorted(dns.resolver.resolve(domain, "MX", lifetime=8), key=lambda r: r.preference)
        host = str(mx[0].exchange).rstrip(".")
    except Exception:
        return {"status": "invalid", "detail": f"{domain} has no mail server"}
    try:
        with smtplib.SMTP(host, 25, timeout=12) as s:
            s.ehlo_or_helo_if_needed()
            if s.mail(probe_from)[0] != 250:
                return {"status": "unknown", "detail": "mail server refused the check"}
            code, msg = s.rcpt(address)
            text = msg.decode(errors="ignore")[:120] if isinstance(msg, bytes) else str(msg)[:120]
            if code in (550, 551, 553) or (code == 450 and "user unknown" in text.lower()):
                return {"status": "invalid", "detail": f"{host}: {code} {text}"}
            if code not in (250, 251):
                return {"status": "unknown", "detail": f"{host}: {code} {text}"}
            fake = f"zz{random.randint(10**8, 10**9)}@{domain}"          # a made-up address: servers that accept it accept everything
            fcode, _ = s.rcpt(fake)
            if fcode in (250, 251):
                return {"status": "unknown", "detail": f"{domain} accepts every address, so this cannot be confirmed"}
            return {"status": "valid", "detail": f"{host} accepts this mailbox"}
    except (socket.timeout, TimeoutError, OSError, smtplib.SMTPException) as e:
        return {"status": "unknown", "detail": f"could not reach {host}:25 ({type(e).__name__}); your network may block this check"}
