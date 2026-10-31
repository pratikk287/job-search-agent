"""One-time sign-in to job sites in a plain (non-automated) Chrome window.

Google refuses "Continue with Google" inside browsers that automation tools control, so signing in is done in an
ordinary Chrome that uses the same profile folder the pre-fill browser later reuses. The sign-in is saved in that
profile; the app never sees or stores your passwords.
"""
import subprocess
from pathlib import Path

from . import config

SITES = {
    "linkedin": "https://www.linkedin.com/login",
    "naukri": "https://www.naukri.com/nlogin/login",
    "shine": "https://www.shine.com/myshine/login/",
    "indeed": "https://secure.indeed.com/auth",
    "iimjobs": "https://www.iimjobs.com/login",
    "wellfound": "https://wellfound.com/login",
    "cutshort": "https://cutshort.io/login",
    "michaelpage": "https://www.michaelpage.co.in/user/login",
}
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def browser_path():
    return next((p for p in CHROME_PATHS if Path(p).exists()), None)


def open_signin(site):
    exe = browser_path()
    if not exe:
        raise RuntimeError("Chrome or Edge was not found")
    if site not in SITES:
        raise ValueError(f"Unknown site {site}")
    profile = config.DATA_DIR / "browser_profile"
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([exe, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", SITES[site]],
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return SITES[site]
