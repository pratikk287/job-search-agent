"""Pre-fill an employer application form in a visible browser. Never presses Submit.

Run by the app after the user approves an 'application' action:  python -m jobagent.autofill <action_id>
"""
import json
import re
import sys
from pathlib import Path

from . import config, db, outreach

LABEL_JS = """el => {
  const txt = s => (s || '').replace(/\\s+/g, ' ').trim();
  let label = '';
  if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) label = l.innerText; }
  if (!label) { const l = el.closest('label'); if (l) label = l.innerText; }
  if (!label && el.getAttribute('aria-labelledby')) {
    label = el.getAttribute('aria-labelledby').split(' ').map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
  }
  if (!label) { const box = el.closest('div, fieldset, li'); if (box) { const l = box.querySelector('label, legend, .label, .text'); if (l) label = l.innerText; } }
  return { label: txt(label), aria: txt(el.getAttribute('aria-label')), name: el.name || '', id: el.id || '',
           placeholder: txt(el.placeholder), type: (el.type || el.tagName).toLowerCase(), required: el.required ||
           el.getAttribute('aria-required') === 'true' || /\\*/.test(label), visible: !!(el.offsetWidth || el.offsetHeight
           || el.getClientRects().length), value: el.value || '' };
}"""


def field_values(job_id):
    prof = config.profile()
    docs = {d["kind"]: d["path"] for d in db.query("SELECT kind, path FROM documents WHERE job_id = ?", (job_id,))}
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    resume = docs.get("resume_pdf") or str(config.ROOT / prof["resumes"][job["resume_key"] or "A"]["file"])
    letter = Path(docs["cover_letter_txt"]).read_text(encoding="utf-8") if docs.get("cover_letter_txt") else ""
    return rules(prof), resume, docs.get("cover_letter_docx"), letter


def rules(prof):
    """(label regex, value) in priority order. A value of None means 'never fill; leave it for the candidate'."""
    ctc = prof.get("current_ctc") or {"total_lpa": prof["current_ctc_lpa"]}
    edu = {e["degree"].split()[0].lower().strip(","): e for e in prof.get("education", [])}   # 'mba', 'bsc'
    mba, bsc = edu.get("mba", {}), edu.get("bsc", {})
    school = prof.get("school_scores", {})
    cities = ", ".join(prof["preferred_locations"] + prof.get("open_locations", []))
    yes = lambda b: "Yes" if b else "No"
    rel = prof.get("relevant_experience") or {}
    exp = lambda area: (str(rel[area]) if rel.get(area) not in (None, "") else None)
    return [
        (r"e-?mail", prof["email"]),          # before the sensitive rule, so "Email address" is not mistaken for an address
        # Personal and sensitive data stays with the candidate.
        (r"date of birth|\bdob\b|birth ?date|aadh?aa?r|\bpan\b|passport|gender|caste|religion|disabilit|veteran|"
         r"ethnic|\brace\b|marital|nationality id|social security|address|pin ?code|postal|zip", None),
        (r"referr(al|er)('s)? (name|email|id)|name of (the )?referr|employee id", None),
        # Relevant experience by area. Only the numbers the candidate entered in Settings are used; blank means "ask me".
        (r"experience.*(product marketing|\bpmm\b|go-to-market|\bgtm\b)", exp("product_marketing")),
        (r"experience.*(growth|performance|digital marketing|acquisition|demand gen)", exp("growth_marketing")),
        (r"experience.*(category|marketplace|e-?commerce|seller|merchandis)", exp("category_marketplace")),
        (r"experience.*(\bproduct\b|\bpm\b)|product (management|manager).*(years|experience)", exp("product_management")),
        (r"(relevant|work|total).*experience.*marketing|marketing experience", exp("marketing")),
        (r"first\s*name|given name|fname", prof["first_name"]),
        (r"last\s*name|surname|family name|lname", prof["last_name"]),
        (r"^(full\s*)?name\b|your name|candidate name|legal name", prof["name"]),
        (r"e-?mail", prof["email"]),
        (r"phone|mobile|contact number", prof["phone"]),
        (r"linkedin", prof.get("linkedin_url")),
        (r"portfolio|personal (site|website)|^website", prof.get("portfolio_url")),
        (r"notice period|when can you (join|start)|earliest (start|joining)", prof.get("notice_period")),
        (r"expected (ctc|salary|compensation|pay)|salary expectation|desired (salary|compensation)",
         f"{prof.get('expected_ctc_lpa') or prof['target_ctc_lpa']['min']} LPA"),
        (r"(current|present) .*fixed|fixed (ctc|salary|component|pay)", f"{ctc.get('fixed_lpa')} LPA" if ctc.get("fixed_lpa") else None),
        (r"(current|present) .*variable|variable (ctc|pay|component)|bonus", f"{ctc.get('variable_lpa')} LPA" if ctc.get("variable_lpa") else None),
        (r"(current|present) (ctc|salary|compensation|pay)|^ctc$",
         f"{ctc['total_lpa']} LPA ({ctc['fixed_lpa']} fixed + {ctc['variable_lpa']} variable)" if ctc.get("fixed_lpa") else f"{ctc['total_lpa']} LPA"),
        (r"current (company|employer|organi[sz]ation)|^company$|^org$|^employer", prof["current_employer"]),
        (r"current (title|designation|role|position)|^designation$", prof.get("current_title")),
        (r"preferred (work )?locations?|location preference", cities),
        (r"current (location|city)|^location|^city|where are you (based|located)", prof["current_city"]),
        (r"^(total )?(years of )?experience( \(years\))?$|total (years of )?(work )?experience|overall experience",
         str(prof["total_experience_years"])),
        (r"sponsor|visa", yes(prof.get("needs_sponsorship", False))),
        (r"authori[sz]ed to work|work authori[sz]ation|legally (eligible|allowed|authori[sz]ed)|right to work",
         "Yes" if prof.get("work_authorization") else None),
        (r"relocat", yes(prof.get("willing_to_relocate"))),
        (r"(comfortable|willing|open|able|okay|ok) .*(office|on-?site|hybrid|remote|work from)", "Yes" if prof.get("work_mode") else None),
        (r"(previously|ever|already) (applied|worked|interviewed)|applied .*(before|in the past|last \d+ months)|former employee|worked (here|for us) before",
         prof.get("previously_applied")),
        (r"(were|are) you referred|referral|employee refer", prof.get("referral")),
        (r"how did you (hear|find|learn|come across)|^source$|where did you (hear|find)", prof.get("how_heard")),
        (r"reason for (leaving|change|switch|looking|job change)|why (are you|do you want to) (leav|chang|switch|look)",
         prof.get("reason_for_leaving")),
        (r"(mba|post ?grad\w*|master'?s?|pg) .*(cgpa|gpa|score|percentage|marks|grade)", mba.get("score")),
        (r"(bsc|b\.sc|bachelor'?s?|graduation|under ?grad\w*|ug) .*(cgpa|gpa|score|percentage|marks|grade)", bsc.get("score")),
        (r"12th|xii\b|hsc|higher secondary|class 12|intermediate", school.get("12th")),
        (r"10th|\bx\b (std|class|grade)|class 10|ssc|secondary school", school.get("10th")),
        (r"\b(cgpa|gpa)\b", mba.get("score")),
        (r"(mba|post ?grad\w*|master'?s?|pg) .*(year|completion|passing|graduat)", str(mba.get("end_year", "")) or None),
        (r"(graduation|passing) year|year of (graduation|passing)", str(bsc.get("end_year", "")) or None),
        (r"highest (degree|qualification|education)", mba.get("degree")),
    ]


def to_field(value, label, ftype):
    """Adapt an answer to the field: numbers for numeric inputs, rupees when the form asks in INR."""
    if value is None:
        return None
    lpa = re.match(r"^(\d+(?:\.\d+)?) LPA", value)
    if lpa and re.search(r"\binr\b|rupees|₹|in rs", label, re.I) and not re.search(r"lakh|lpa|lac", label, re.I):
        return str(int(float(lpa.group(1)) * 100000))
    if ftype == "number":
        m = re.search(r"\d+(?:\.\d+)?", value)
        return m.group(0) if m else None
    return value


def choose_option(options, value):
    """Best <select>/radio option for an answer, or None."""
    v = (value or "").strip().lower()
    opts = [(o, o.strip().lower()) for o in options if o and o.strip() and not re.match(r"^(select|choose|--|please)", o.strip(), re.I)]
    for o, ol in opts:
        if ol == v:
            return o
    if v in ("yes", "no"):
        return next((o for o, ol in opts if ol.startswith(v)), None)
    days = re.match(r"(\d+) days", v)
    if days:
        n = int(days.group(1))
        for o, ol in opts:
            nums = [int(x) for x in re.findall(r"\d+", ol)]
            if (n == 30 and re.search(r"1 month|one month", ol)) or (nums and (n in nums or (len(nums) == 2 and nums[0] <= n <= nums[1]))):
                return o
    return next((o for o, ol in opts if ol in v or v in ol), None)


def answer_for(question, rule_list):
    q = (question or "").lower()
    for rx, v in rule_list:
        if re.search(rx, q):
            return True, v
    return False, None


CHOICES_JS = """root => {
  const txt = s => (s || '').replace(/\\s+/g, ' ').trim();
  const question = el => {
    const fs = el.closest('fieldset'); if (fs && fs.querySelector('legend')) return txt(fs.querySelector('legend').innerText);
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && el.tagName === 'SELECT') return txt(l.innerText); }
    const box = el.closest('.application-question, li, [class*=question], [class*=field], div');
    const l = box && box.querySelector('label, legend, .text, [class*=label]');
    return txt(l ? l.innerText : el.getAttribute('aria-label') || el.name);
  };
  const out = [];
  document.querySelectorAll('select').forEach((s, i) => {
    if (s.value && s.selectedIndex > 0) return;
    s.dataset.jaIdx = 's' + i;
    out.push({ kind: 'select', idx: 's' + i, question: question(s), options: [...s.options].map(o => txt(o.text)),
               required: s.required || /\\*/.test(question(s)) });
  });
  const groups = {};
  document.querySelectorAll('input[type=radio]').forEach(r => { (groups[r.name] = groups[r.name] || []).push(r); });
  Object.entries(groups).forEach(([name, radios]) => {
    if (radios.some(r => r.checked)) return;
    const labelOf = r => { const l = (r.id && document.querySelector(`label[for="${CSS.escape(r.id)}"]`)) || r.closest('label'); return txt(l ? l.innerText : r.value); };
    out.push({ kind: 'radio', name, question: question(radios[0]), options: radios.map(labelOf),
               required: radios.some(r => r.required) || /\\*/.test(question(radios[0])) });
  });
  return out;
}"""


def fill_choices(frame, rule_list, report):
    """Dropdowns and radio groups (yes/no questions, notice period buckets...)."""
    try:
        items = frame.evaluate(CHOICES_JS, None)
    except Exception:
        return
    for it in items:
        matched, value = answer_for(it["question"], rule_list)
        pick = choose_option(it["options"], value) if value else None
        if not pick:
            if it["required"] or matched:
                report["needs_you"].append(it["question"])
            continue
        try:
            if it["kind"] == "select":
                frame.select_option(f'select[data-ja-idx="{it["idx"]}"]', label=pick)
            else:
                i = it["options"].index(pick)
                frame.locator(f'input[type=radio][name="{it["name"]}"]').nth(i).check()
            report["filled"].append({"field": it["question"], "value": pick})
        except Exception as e:
            report["notes"].append(f"Could not choose '{pick}' for {it['question']}: {e}")


LOGIN_URL = re.compile(r"authwall|/login|/signin|/sign-in|/signup|/sign-up|/register|nlogin|accounts\.google|/checkpoint|uas/", re.I)
SUBMIT_WORDS = re.compile(r"^\s*(submit|send|finish)", re.I)


def on_login_wall(page):
    """Login / sign-up pages must never be filled: the candidate signs in by hand, once."""
    if LOGIN_URL.search(page.url):
        return True
    try:
        return bool(page.query_selector("input[type=password]")) and not page.query_selector("input[type=file]")
    except Exception:
        return False


def _status(action_id, msg, report):
    db.update("actions", "id", action_id, {"result": json.dumps({"instructions": msg, **report})})


def fill_page(page, rules, resume, letter_file, letter_text, report):
    frames = [page] + [f for f in page.frames if f != page.main_frame and re.search(r"greenhouse|lever|ashby|workable", f.url or "")]
    for frame in frames:
        fill_choices(frame, rules, report)
        for el in frame.query_selector_all("input, textarea"):
            try:
                info = el.evaluate(LABEL_JS)
            except Exception:
                continue
            if not info["visible"] and info["type"] != "file":
                continue
            key = " ".join(x for x in (info["label"], info["aria"], info["placeholder"], info["name"].replace("_", " "),
                                       info["id"].replace("_", " ")) if x).lower()
            if info["type"] == "password":
                continue
            if info["type"] == "file":
                if el.evaluate("e => e.files && e.files.length > 0"):
                    continue
                target = letter_file if re.search(r"cover", key) else resume
                if target and Path(target).exists():
                    try:
                        el.set_input_files(target)
                        report["files"].append({"field": info["label"] or info["name"], "file": Path(target).name})
                    except Exception as e:
                        report["notes"].append(f"Upload failed for {info['label'] or info['name']}: {e}")
                continue
            if info["type"] in ("checkbox", "radio", "hidden", "submit", "button", "search"):
                if info["required"] and info["type"] == "checkbox":
                    report["needs_you"].append(info["label"] or info["name"])
                continue    # radio groups are handled by fill_choices
            if info["value"]:
                continue
            value = None
            if info["type"] == "textarea" and re.search(r"cover letter|why .*(join|interested|role)|additional information", key):
                value = letter_text
            else:
                _, value = answer_for(info["label"] or key, rules)
                value = to_field(value, info["label"] or key, info["type"])
            if value:
                try:
                    el.fill(value)
                    report["filled"].append({"field": info["label"] or info["name"], "value": value[:60]})
                except Exception as e:
                    report["notes"].append(f"Could not fill {info['label']}: {e}")
            elif info["required"]:
                report["needs_you"].append(info["label"] or info["name"] or info["placeholder"])


def open_application(page, ctx):
    """Click the site's Apply / Easy Apply button. Returns the page holding the form (may be a new tab)."""
    if page.evaluate("""() => [...document.querySelectorAll('input[type=email], input[name*=email i], input[id*=email i], input[id*=name i]')]
                          .some(e => e.offsetWidth || e.offsetHeight)"""):
        return page       # form already visible
    candidates = ["button.jobs-apply-button", "button:has-text('Easy Apply')", "a:has-text('Apply on company website')",
                  "button:has-text('Apply on company site')", "a:has-text('Apply on company site')", "button:has-text('Apply now')",
                  "a:has-text('Apply now')", "button:has-text('Apply')", "a:has-text('Apply')", "text=/^apply( for this job)?$/i"]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            if not loc.is_visible(timeout=800):
                continue
            pages_before = len(ctx.pages)
            loc.click(timeout=3000)
            page.wait_for_timeout(3500)
            if len(ctx.pages) > pages_before:       # "apply on company site" opened a new tab
                new = ctx.pages[-1]
                new.wait_for_load_state("domcontentloaded")
                new.wait_for_timeout(2500)
                return new
            return page
        except Exception:
            continue
    return page


def step_through(page, fill, report, max_steps=8):
    """Multi-step forms (LinkedIn Easy Apply, Naukri, Workday): fill each step and press Next/Continue/Review.
    Stops before any Submit button; that click is always the candidate's."""
    for _ in range(max_steps):
        fill()
        nxt = None
        for sel in ("button:has-text('Next')", "button:has-text('Continue')", "button:has-text('Review')",
                    "button[aria-label*='next' i]", "button[aria-label*='continue' i]", "button[aria-label*='review' i]"):
            loc = page.locator(sel).first
            try:
                if loc.is_visible(timeout=500) and not SUBMIT_WORDS.search(loc.inner_text(timeout=500) or ""):
                    nxt = loc
                    break
            except Exception:
                continue
        if not nxt:
            return
        before = page.content()[:20000]
        try:
            nxt.click(timeout=3000)
            page.wait_for_timeout(2000)
        except Exception:
            return
        if page.content()[:20000] == before:     # a required field is blocking the step
            report["notes"].append("Stopped at a step with a required question only you can answer.")
            return


DEBUG_PORT = 9223


def _port_open(port):
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def plain_chrome(p):
    """A normal Chrome window (not started by an automation tool) that the app attaches to only to type into forms.
    Sites' anti-bot checks, and Google sign-in, treat it like the Chrome you use yourself."""
    import subprocess
    import time
    from . import signin
    if not _port_open(DEBUG_PORT):
        exe = signin.browser_path()
        if not exe:
            raise RuntimeError("Chrome or Edge was not found")
        profile = config.DATA_DIR / "browser_profile"
        subprocess.Popen([exe, f"--user-data-dir={profile}", f"--remote-debugging-port={DEBUG_PORT}", "--no-first-run",
                          "--no-default-browser-check", "--start-maximized", "about:blank"],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        for _ in range(30):
            if _port_open(DEBUG_PORT):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Chrome is already open with the job-sites profile (the sign-in window?). Close that Chrome window, "
                               "then press 'Open pre-filled form' again.")
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
    return browser.contexts[0]


def run(action_id, headless=False):
    from playwright.sync_api import sync_playwright
    a = outreach.get(action_id)
    job = db.one("SELECT * FROM jobs WHERE id = ?", (a["job_id"],))
    rules, resume, letter_file, letter_text = field_values(job["id"])
    url = a["recipient"]
    if "jobs.lever.co" in url and not url.rstrip("/").endswith("/apply"):
        url = url.rstrip("/") + "/apply"
    if "jobs.ashbyhq.com" in url and "/application" not in url:
        url = url.rstrip("/") + "/application"
    report = {"url": url, "filled": [], "files": [], "needs_you": [], "notes": []}
    log = config.DATA_DIR / f"autofill_{action_id}.json"
    site = re.sub(r"^https?://(www\.|in\.)?", "", url).split("/")[0]

    with sync_playwright() as p:
        if headless:       # tests only
            ctx = p.chromium.launch_persistent_context(str(config.DATA_DIR / "browser_profile"), channel="chrome", headless=True, no_viewport=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
        else:
            ctx = plain_chrome(p)
            page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3500)

        # 1. Sign-in wall: wait (up to 10 minutes) while the candidate signs in by hand, then continue.
        if on_login_wall(page) and not headless:
            _status(action_id, f"{site} needs you to sign in. Sign in in the Chrome window that is open (Google sign-in works there). "
                               f"The form is filled automatically right after.", report)
            for _ in range(300):
                page.wait_for_timeout(2000)
                if page.is_closed():
                    return
                if not on_login_wall(page):
                    break
            else:
                _status(action_id, f"Still waiting for sign-in to {site}. Press 'Open pre-filled form' again when ready.", report)
                return
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)
            report["notes"].append(f"Signed in to {site}.")
        elif on_login_wall(page):
            report["notes"].append(f"{site} shows a sign-in page; nothing was filled.")

        # 2. Open the application form and fill it, step by step.
        if not on_login_wall(page):
            page = open_application(page, ctx)
            if on_login_wall(page):
                report["notes"].append(f"The employer's site ({re.sub(r'^https?://', '', page.url).split('/')[0]}) wants you to sign "
                                       "in or create an account there. Do that in this window, then fill the remaining fields.")
            else:
                step_through(page, lambda: fill_page(page, rules, resume, letter_file, letter_text, report), report)

        report["needs_you"] = list(dict.fromkeys(report["needs_you"]))
        report["notes"].append("Submit was NOT pressed. Review every field, then press Submit on the site yourself.")
        log.write_text(json.dumps(report, indent=2), encoding="utf-8")
        _status(action_id, "Form pre-filled in the browser window. Review it, press the site's Submit button, then press "
                           "'Mark submitted' here.", report)
        db.log_event(job["id"], "application_prefilled", {"filled": len(report["filled"]), "needs_you": report["needs_you"]})
        if headless:   # test mode: report whether anything submitted the form, then exit
            report["submitted"] = bool(page.evaluate("() => !!window.SUBMITTED"))
            log.write_text(json.dumps(report, indent=2), encoding="utf-8")
            ctx.close()
            return report
        # The Chrome window stays open for you to review and submit; this script just disconnects from it.


if __name__ == "__main__":
    try:
        run(int(sys.argv[1]), headless="--headless" in sys.argv)
    except Exception as e:
        db.update("actions", "id", int(sys.argv[1]), {"status": "failed", "result": json.dumps({"error": f"Pre-fill failed: {e}"})})
        raise
