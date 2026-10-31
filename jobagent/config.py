"""Paths, settings, profile and secrets."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "documents"
REPORTS_DIR = DATA_DIR / "reports"
WEB_DIR = ROOT / "web"
DB_PATH = DATA_DIR / "jobagent.db"

for _d in (DATA_DIR, DOCS_DIR, REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_env():
    values = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_ENV_FILE = _load_env()


def secret(name):
    """Values from the project's .env file win. Anthropic credentials come ONLY from .env, so the app never
    borrows credentials that some other tool (e.g. a coding assistant) set in the process environment."""
    if name.startswith("ANTHROPIC_"):
        return _ENV_FILE.get(name, "").strip()
    return (_ENV_FILE.get(name) or os.environ.get(name, "")).strip()


def set_env(updates):
    """Write keys into .env (creating it if needed) and reload them. Values are never logged."""
    env_file = ROOT / ".env"
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            done.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _ENV_FILE.update(updates)


def _read_json(name):
    path = CONFIG_DIR / name
    if not path.exists() and (CONFIG_DIR / name.replace(".json", ".example.json")).exists():
        path = CONFIG_DIR / name.replace(".json", ".example.json")      # fresh install: start from the template
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(name, data):
    (CONFIG_DIR / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def settings():
    return _read_json("settings.json")


def save_settings(data):
    _write_json("settings.json", data)


def profile():
    return _read_json("profile.json")


def pitch():
    """Short first-person lines for LinkedIn notes and emails, taken from the profile (fallback: title and employer)."""
    p = profile()
    emp, title = p.get("current_employer") or "", p.get("current_title") or "my current role"
    base = (f"I work as {title} at {emp}." if emp else f"I work as {title}.")
    return {"hr": p.get("note_intro_hr") or base, "team": p.get("note_intro_team") or base, "employer": emp or "my current company",
            "extras": (p.get("extras_line") or "").strip()}


def save_profile(data):
    _write_json("profile.json", data)


def companies():
    return _read_json("companies.json")["companies"]


def save_companies(items):
    data = _read_json("companies.json")
    data["companies"] = items
    _write_json("companies.json", data)


def integrations():
    """Which optional integrations have credentials. Never returns the values."""
    return {
        "claude": bool(secret("ANTHROPIC_API_KEY")),
        "jsearch": bool(secret("RAPIDAPI_KEY")),
        "adzuna": bool(secret("ADZUNA_APP_ID") and secret("ADZUNA_APP_KEY")),
        "apify": bool(secret("APIFY_TOKEN")),
        "jooble": bool(secret("JOOBLE_API_KEY")),
        "naukri": bool(secret("FIRECRAWL_API_KEY")),
        "indeed": bool(secret("FIRECRAWL_API_KEY")),
        "hunter": bool(secret("HUNTER_API_KEY")),
        "apollo": bool(secret("APOLLO_API_KEY")),
        "smtp": bool(secret("SMTP_USER") and secret("SMTP_PASSWORD")),
        "imap": bool(secret("SMTP_USER") and secret("SMTP_PASSWORD") and secret("IMAP_HOST")),
    }
