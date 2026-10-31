"""Shared helpers for job sources: HTTP, HTML-to-text, dates, locations, salary."""
import html
import re
from datetime import datetime, timedelta, timezone

import requests
from lxml import html as lxml_html

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36 JobAgent/1.0 (personal job search)")

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"})


def get_json(url, timeout=20, **kw):
    r = session.get(url, timeout=timeout, **kw)
    r.raise_for_status()
    return r.json()


def post_json(url, payload, timeout=20, **kw):
    r = session.post(url, json=payload, timeout=timeout, **kw)
    r.raise_for_status()
    return r.json()


def html_to_text(raw):
    if not raw:
        return ""
    raw = html.unescape(raw) if "&lt;" in raw else raw
    try:
        doc = lxml_html.fromstring(f"<div>{raw}</div>")
        for br in doc.iter("br"):
            br.tail = "\n" + (br.tail or "")
        for el in doc.iter("p", "li", "div", "h1", "h2", "h3", "h4", "tr"):
            el.tail = "\n" + (el.tail or "")
        for li in doc.iter("li"):
            li.text = "• " + (li.text or "")
        text = doc.text_content()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


# ------------------------------------------------------------------ dates

def utcnow():
    return datetime.now(timezone.utc)


def to_iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_date(value):
    """Accepts ISO strings, epoch seconds/ms, 'October 12, 2026', '2026-10-12'. Returns ISO UTC or None."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e11 else value
        return to_iso(datetime.fromtimestamp(ts, tz=timezone.utc))
    s = str(value).strip()
    try:
        return to_iso(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return to_iso(datetime.strptime(s, fmt))
        except ValueError:
            continue
    return parse_relative(s)


def parse_relative(s):
    """'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago', '2 weeks ago', '30+ days ago'."""
    s = (s or "").lower()
    if "30+" in s:
        return to_iso(utcnow() - timedelta(days=31))
    if "today" in s or "just now" in s or re.search(r"\b\d+\s*(hours?|hrs?|minutes?|mins?)\b", s):
        return to_iso(utcnow())
    if "yesterday" in s:
        return to_iso(utcnow() - timedelta(days=1))
    m = re.search(r"(\d+)\+?\s*(day|week|month)s?", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * {"day": 1, "week": 7, "month": 30}[unit]
        return to_iso(utcnow() - timedelta(days=days))
    return None


def age_days(iso):
    if not iso:
        return None
    dt = datetime.fromisoformat(iso)
    return max(0.0, (utcnow() - dt).total_seconds() / 86400)


# ------------------------------------------------------------------ locations

CITY_ALIASES = {
    "Bengaluru": ["bengaluru", "bangalore", "blr"],
    "Pune": ["pune"],
    "Gurugram": ["gurugram", "gurgaon"],
    "Mumbai": ["mumbai", "bombay", "navi mumbai", "thane", "powai", "andheri"],
    "Delhi NCR": ["new delhi", "delhi", "noida", "ncr", "faridabad", "ghaziabad"],
    "Hyderabad": ["hyderabad", "secunderabad"],
    "Chennai": ["chennai"],
    "Kolkata": ["kolkata"],
    "Ahmedabad": ["ahmedabad", "gandhinagar"],
    "Jaipur": ["jaipur"],
    "Kochi": ["kochi", "cochin"],
    "Chandigarh": ["chandigarh", "mohali"],
    "Indore": ["indore"],
    "Coimbatore": ["coimbatore"],
}
_CITY_RE = [(city, re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b", re.I)) for city, names in CITY_ALIASES.items()]


def cities_in(text):
    return [city for city, rx in _CITY_RE if rx.search(text or "")]


def is_india(text, country=None):
    if country and str(country).strip().upper() in ("IN", "IND", "INDIA"):
        return True
    t = (text or "").lower()
    return bool(re.search(r"\bindia\b", t)) or bool(cities_in(t))


def is_remote(text):
    return bool(re.search(r"\bremote\b|work from home|\bwfh\b", text or "", re.I))


def pick_city(text, preferred):
    found = cities_in(text)
    for p in preferred:
        if p in found:
            return p
    if found:
        return found[0]
    return "Remote" if is_remote(text) else None


# ------------------------------------------------------------------ salary

def parse_salary_lpa(text):
    """Find an INR salary range in free text. Returns (min_lpa, max_lpa, matched_text) or (None, None, None)."""
    if not text:
        return None, None, None
    t = text.replace("₹", "₹")
    m = re.search(r"(\d{1,2}(?:\.\d+)?)\s*(?:-|–|to)\s*(\d{1,2}(?:\.\d+)?)\s*(?:lpa|lakhs?|lacs?|l\.?p\.?a\.?)\b", t, re.I)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if 1 <= lo <= hi <= 200:
            return lo, hi, m.group(0)
    m = re.search(r"(?:₹|inr|rs\.?)\s*([\d,]{6,})\s*(?:-|–|to)\s*(?:₹|inr|rs\.?)?\s*([\d,]{6,})", t, re.I)
    if m:
        lo, hi = (int(x.replace(",", "")) / 1e5 for x in m.groups())
        if 1 <= lo <= hi <= 200:
            return round(lo, 1), round(hi, 1), m.group(0)
    m = re.search(r"(?:up to|upto|max(?:imum)?)\s*(\d{1,2}(?:\.\d+)?)\s*(?:lpa|lakhs?)", t, re.I)
    if m:
        return None, float(m.group(1)), m.group(0)
    return None, None, None


def salary_from_numbers(lo, hi, currency="INR", period="YEAR"):
    """Structured salary (annual INR) to LPA."""
    if currency and currency.upper() not in ("INR", "₹"):
        return None, None
    mult = {"YEAR": 1, "YEARLY": 1, "ANNUAL": 1, "MONTH": 12, "MONTHLY": 12, "HOUR": 2080}.get((period or "YEAR").upper(), 1)
    conv = lambda v: round(v * mult / 1e5, 1) if v else None
    lo, hi = conv(lo), conv(hi)
    if (lo and lo > 300) or (hi and hi > 300):
        return None, None
    return lo, hi


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def dedupe_key(company, title, city):
    company = re.sub(r"\b(private|pvt|limited|ltd|inc|llp|technologies|india)\b", "", norm(company))
    return f"{re.sub(' +', ' ', company).strip()}|{norm(title)}|{norm(city)}"
