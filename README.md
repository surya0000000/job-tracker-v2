# Job & Internship Application Tracker

Automatically reads your Gmail and tracks every job and internship you've applied to. Syncs to a private Google Sheet with color-coded stages, deduplication, and daily automation via GitHub Actions.

## What It Does

- **Connects to Gmail** and finds application-related emails from the last 8 months (first run) or recent emails (daily runs)
- **Smart filtering** — distinguishes real application responses from job alerts, LinkedIn notifications, recruiter outreach, and newsletters
- **No duplicates** — one application = one row that updates as it progresses (e.g., 6 emails from Google = 1 row)
- **Full lifecycle tracking** — Applied → In Review → OA/Assessment → Phone Screen → Interview Scheduled → Interviewed → Offer → Rejected → Withdrawn
- **Runs automatically** every day at 8 AM UTC via GitHub Actions

## Google Sheet Layout

| Tab | Contents |
|-----|----------|
| **Applications** | All applications, color-coded by stage, sorted by most recent. Columns: Company, Role, Stage, Type, Date Applied, Last Updated, Notes |
| **Summary** | Total applications, active pipeline, interview rate, offer rate, rejection rate, stage breakdown, monthly breakdown |
| **Sync Log** | Every run: timestamp, emails scanned, new apps, status updates, skipped count and reasons |

## Setup (Under 20 Minutes)

### 1. Get a Gemini API Key

1. Go to [aistudio.google.com](https://aistudio.google.com)
2. Create a free API key
3. Save it — you'll add it as a GitHub secret

### 2. Enable Google APIs

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or select existing)
3. Enable these APIs:
   - Gmail API
   - Google Sheets API
   - Google Drive API

### 3. Create OAuth Credentials

1. In Cloud Console: **APIs & Services** → **Credentials** → **Create Credentials** → **OAuth client ID**
2. Application type: **Desktop app**
3. Download the JSON file
4. Rename it to `credentials.json` and place it in this project folder

### 4. First-Time Local Run (Required)

Run locally once to authenticate and build your 8-month history:

```bash
pip install -r requirements.txt
python run.py --initial
```

- A browser will open for Google sign-in — authorize the app
- `token.json` will be created (never commit this)
- A new Google Sheet will be created
- The script will print the **Spreadsheet ID** — save it for GitHub secrets

### 5. Push to GitHub and Add Secrets

1. Push this repo to your GitHub account
2. Go to **Settings** → **Secrets and variables** → **Actions**
3. Add these 4 secrets:

| Secret | Value |
|-------|-------|
| `GEMINI_API_KEY` | Your Gemini API key from step 1 |
| `GOOGLE_CREDENTIALS` | Full contents of `credentials.json` (paste entire JSON) |
| `GOOGLE_TOKEN` | Full contents of `token.json` (created after first run) |
| `SPREADSHEET_ID` | The ID printed after first run (from the sheet URL) |

### 6. Done

Daily runs will execute automatically at 8 AM UTC. You can also trigger manually from the **Actions** tab.

## Commands

```bash
# Initial run (scan 8 months, create sheet)
python run.py --initial

# Daily incremental sync (default)
python run.py

# Recreate sheet from local DB (if sheet was deleted)
python run.py --export
```

## After Every Sync

The script prints:
- **Google Sheet URL** — open from any device
- **Excel Download URL** — download as .xlsx anytime

## Debugging

- **errors.log** — Every skip and rejection is logged with the exact reason. In CI, this is uploaded as a downloadable artifact (Actions → run → Artifacts).
- **Sync Log tab** — Shows what happened in each run: emails scanned, new apps, updates, skips.

## What Never Gets Committed

- `credentials.json`
- `token.json`
- `.env`
- `applications.db`
- `errors.log`

## How It Works

1. **Pre-filter** — Instant rules discard obvious junk (job alerts, personal domains, etc.) before any AI calls
2. **AI parsing** — Gemini 1.5 Flash classifies each email; low confidence or missing company/role → skip
3. **Deduplication** — Company + role matching with normalization (e.g., "Google LLC" = "Google", "SWE" = "Software Engineer") so one application = one row
4. **Stage upgrade** — Only moves forward (never downgrades); Rejected/Withdrawn always apply

## Timezone

The workflow runs at 8:00 AM UTC. To change it, edit `.github/workflows/daily-sync.yml` and adjust the cron expression (e.g., `0 13 * * *` for 8 AM EST).
