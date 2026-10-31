# Instructions for Claude Code: set up and run the Job Search Agent

This repository is a local app (Python + FastAPI + SQLite, plain JS dashboard) that finds fresh jobs in India,
scores them against the user's resumes, pre-fills application forms in Chrome, finds HR / hiring-team people,
and sends outreach emails from the user's Gmail. **The user always presses Submit on forms themselves.**
When the user says "set this up", "get started", "run it" or similar, do the steps below in order.
Be brief with the user, do the work yourself, and only ask for what only they can provide.

## 0. Rules (do not break these)
- Never invent profile facts. Every value in `config/profile.json` must come from the user's resume or their answers.
- Never type or store passwords. Gmail uses an app password the user pastes into the app's Settings; portal sign-ins
  happen in the Chrome window the app opens. Never ask for or echo secrets in chat; `.env` is git-ignored.
- Never press Submit on an application form, never send LinkedIn messages, never solve captchas.
- Do not commit `.env`, `data/`, `resumes/`, or `config/profile.json` (already in `.gitignore`).
- Tell the user honestly that scraping Naukri/Indeed via Firecrawl is against those sites' terms; it is optional and low volume.

## 1. Check the machine
- Windows 10/11 is what this was built and tested on. macOS/Linux: the core app runs, but the desktop shortcut,
  automatic closing of a stuck port and some Chrome launching are Windows-only (note this to the user if they are not on Windows).
- Need Python 3.10+ (`python --version`; on Windows also try `py --version`) and Google Chrome installed.
  If Python is missing, tell the user to install it from python.org (tick "Add python.exe to PATH") and restart the terminal.

## 2. Install
```bash
pip install -r requirements.txt
```
(No browser download is needed: the app drives the user's own installed Chrome.)

## 3. Create the user's profile
1. Ask the user to put their resume PDF(s) in `resumes/` (one is fine; two lets the app pick the better fit per job).
2. Copy `config/profile.example.json` to `config/profile.json`.
3. Read the resume(s) and fill every field of `config/profile.json` from them: name, email, phone, LinkedIn URL,
   portfolio URL, current city, current employer and title, total experience, education, `resumes.A` / `resumes.B`
   (`label` and `file` path under `resumes/`, `functions` = which role families that resume suits).
   Delete key `_template`. If there is only one resume, point both A and B at the same file.
4. Ask the user (one short message, all questions together) for what a resume does not say: current and expected CTC
   (`current_ctc`, `expected_ctc_lpa`, `target_ctc_lpa`), notice period, preferred cities (`preferred_locations`,
   `open_locations`), willing to relocate, work mode, and 1 sentence each for `note_intro_hr` and `note_intro_team`
   (how they introduce themselves in a LinkedIn note, under 90 characters, e.g. "I own growth at Acme."),
   plus an optional `extras_line` (one sentence of extra credentials for emails). Leave `relevant_experience` empty
   unless they give numbers they can stand behind.
5. Target roles: in `config/settings.json` set `search_keywords` (portal search phrases) and `scan_cities`.
   The role families that decide relevance live in `jobagent/keywords.py` (`FUNCTIONS`, exclusion patterns).
   They are written for product / marketing / growth / category roles. If the user targets other roles
   (for example data, sales, operations, design), edit `FUNCTIONS` and the title exclusions there to match, and update
   `target_functions` and each resume's `functions` in the profile. Run `python tests/test_core.py` afterwards.

## 4. Keys (all optional; ask which the user has, explain free tiers)
Copy `.env.example` to `.env`. Never paste secrets into chat logs unnecessarily; the user can paste them into `.env` themselves.
| Key | What it unlocks | Free tier | Where |
|---|---|---|---|
| none | 46 company career sites (free, about half of all searches) | unlimited | built in |
| `APIFY_TOKEN` | LinkedIn jobs | $5/month, no card | console.apify.com/settings/integrations |
| `FIRECRAWL_API_KEY` | Naukri + Indeed pages, and finding HR / team people on LinkedIn | 1,000 credits | firecrawl.dev |
| `RAPIDAPI_KEY` | JSearch (many portals via Google for Jobs) | 200 searches/month | rapidapi.com, subscribe to "JSearch" |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Adzuna India | free | developer.adzuna.com |
| `JOOBLE_API_KEY` | Jooble | 500 requests total | jooble.org/api/about |
| Gmail | sending emails, bounce and reply tracking | free | connect inside the app: Settings > Gmail (needs a Google app password) |

The app works with none of these (company sites only), and the dashboard's Settings shows which are active.
Without `FIRECRAWL_API_KEY` the app cannot find HR / hiring-team people.

## 5. Start the app
```bash
python run.py
```
It waits until the server answers, then opens http://127.0.0.1:8765 in the browser. Keep this process running
(run it in the background in Claude Code). If port 8765 is held by a stuck copy of the app, `run.py` closes it (Windows).
On Windows, offer the Desktop shortcut: `powershell -ExecutionPolicy Bypass -File make_shortcut.ps1`.

## 6. First run with the user
1. Open Settings (gear icon) > "Confirm your details" and have them confirm the highlighted items.
2. Settings > "Sign in to job sites": sign in once to LinkedIn / Naukri etc. in the Chrome window that opens (Continue with Google works), then close that window.
3. Settings > Gmail: paste the Google app password (myaccount.google.com/apppasswords; 2-step verification must be on). The app tests it before saving.
4. Press **Scan for jobs** (a scan takes a few minutes; there is a progress bar and a Stop button).
5. Open a job, press **Auto-fill & apply**. Chrome opens the form pre-filled. The user checks it and presses Submit.
   The app then lists HR / hiring-team people with the emails it would use: the user can fix any address, and presses Send.

## Layout (for changes)
`jobagent/` server code (`server.py` routes, `scan.py`, `scoring.py`, `verify.py`, `apply.py`, `autofill.py`, `people.py`,
`contacts.py`, `outreach.py`, `mix.py`, `sources/` one file per job source), `web/` dashboard, `config/` settings, profile and the company list,
`tests/test_core.py` (run `python tests/test_core.py`), `data/` created at runtime (database, logs, saved sign-ins).
After editing Python or `.env`, restart the app. Keep changes small, run the tests, and do not weaken the safety rules in section 0.
