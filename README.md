# Job Search Agent

A local app that finds fresh jobs in India, ranks them against your resumes, opens each application form pre-filled
in Chrome, finds the HR and hiring-team people for the role, writes a personal LinkedIn note for each, and emails
them from your Gmail. You press Submit on every application and you send every LinkedIn message yourself.

- Jobs from 46 company career sites (free) plus LinkedIn, Naukri, Indeed and other portals, within a strict credit budget
- Only roles posted in the last 10 days; scam and closed-listing checks; 0 to 100 fit score
- One click: **Auto-fill & apply** (form pre-filled, people found, emails reviewed and sent)
- Bounce and reply tracking, learns each company's email format
- Dashboard with a Main tab and a Sheet tab (LinkedIn outreach), light and dark mode
- Everything stays on your computer. Nothing is sent without you pressing a button.

## Quick start with Claude Code (easiest)

1. Install [Claude Code](https://claude.com/claude-code), [Python 3.10+](https://www.python.org/downloads/) (tick "Add to PATH") and Google Chrome.
2. In a terminal:

```bash
git clone https://github.com/YOUR-USERNAME/job-search-agent.git
cd job-search-agent
claude
```

3. Put your resume PDF(s) in the `resumes` folder, then tell Claude: **"Set this up for me."**
   It reads `CLAUDE.md`, installs everything, fills your profile from your resume, asks you a few questions
   (salary, cities, notice period), and starts the app.

## Manual setup (without Claude Code)

```bash
pip install -r requirements.txt
copy config\profile.example.json config\profile.json     # macOS/Linux: cp
copy .env.example .env                                     # macOS/Linux: cp
```

1. Edit `config/profile.json` with your details and resume file names (put the PDFs in `resumes/`).
2. Edit `config/settings.json`: `search_keywords` and `scan_cities`.
3. Optional keys in `.env` (see `.env.example`; every key is optional, company sites need none).
4. `python run.py` opens http://127.0.0.1:8765. Windows desktop shortcut: `powershell -ExecutionPolicy Bypass -File make_shortcut.ps1`.
5. In the app: Settings > confirm details, sign in to job sites, connect Gmail (Google app password), then **Scan for jobs**.

## Free keys (all optional)

| Key | Unlocks | Free tier |
|---|---|---|
| `APIFY_TOKEN` | LinkedIn jobs | $5 a month |
| `FIRECRAWL_API_KEY` | Naukri, Indeed, finding HR/team people | 1,000 credits |
| `RAPIDAPI_KEY` | JSearch (many portals) | 200 searches a month |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Adzuna India | free |
| `JOOBLE_API_KEY` | Jooble | 500 requests |

Half of all searches go to company career sites; when credits run low the app shifts more searches to those free sources.

## Good to know

- Built and tested on **Windows 10/11 with Chrome**. macOS and Linux run the core app; the shortcut and some Chrome launching are Windows-only.
- The role families in `jobagent/keywords.py` are written for product, marketing, growth, category and e-commerce roles. For other kinds of roles, edit them (Claude Code can do this for you).
- Guessed email addresses can bounce. The app tracks bounces and learns, and you can fix any address before sending.
- Using Firecrawl on Naukri and Indeed goes against those sites' terms of service. It is optional and low volume; your choice.
- Run the checks: `python tests/test_core.py`

## Safety

The app never presses Submit, never sends LinkedIn messages, never solves captchas, never fills date of birth, ID numbers, address, gender or consent boxes,
and never stores your passwords. Secrets live in `.env`, which is git-ignored along with your resumes, profile and data.
