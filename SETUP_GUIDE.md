# Setup Guide — Step by Step (Beginner Friendly)

Follow these steps in order. Total time: about 15–20 minutes.

---

## Prerequisites

- A computer with **Python 3** installed ([python.org](https://python.org))
- A **Google account** (Gmail)
- A **GitHub account** (free)

---

## Step 1: Get a Free AI API Key (2 minutes)

You need one of these — both are free, no credit card.

### Option A: Groq (recommended — more free requests)

1. Open: **https://console.groq.com**
2. Sign up with Google or email
3. Click **“Create API Key”**
4. Copy the key (starts with `gsk_`)

### Option B: Gemini

1. Open: **https://aistudio.google.com/apikey**
2. Sign in with your Google account
3. Click **“Create API Key”**
4. Copy the key (starts with `AIza`)

Save this key somewhere for Step 5.

---

## Step 2: Set Up Google Cloud (5 minutes)

1. Go to: **https://console.cloud.google.com**
2. Click the project dropdown → **New Project**
   - Name it something like `Job Tracker`
   - Click **Create**
3. Enable APIs:
   - Open **APIs & Services** → **Library**
   - Search for **Gmail API** → enable
   - Search for **Google Sheets API** → enable
   - Search for **Google Drive API** → enable
4. Create credentials:
   - Go to **APIs & Services** → **Credentials**
   - Click **Create Credentials** → **OAuth client ID**
   - If asked, set up the OAuth consent screen:
     - User type: **External** → Create
     - App name: `Job Tracker` → Save
     - Add your own email as a test user → Save
   - Back to **Create Credentials** → **OAuth client ID**
   - Application type: **Desktop app**
   - Name: `Job Tracker` → **Create**
5. Download the JSON:
   - Click the **Download** icon next to your new OAuth client
   - Rename the file to **`credentials.json`**
   - Move it into your project folder (the folder with `run.py`)

---

## Step 3: Get the Code (1 minute)

If you don’t have the project yet:

```bash
git clone https://github.com/YOUR_USERNAME/job-tracker-v2.git
cd job-tracker-v2
```

Or use the folder where you already have the project.

---

## Step 4: Install Dependencies (2 minutes)

Open a terminal in the project folder and run:

```bash
pip install -r requirements.txt
```

If you see “command not found”, try:

```bash
pip3 install -r requirements.txt
```

---

## Step 5: Create Your `.env` File (1 minute)

1. In the project folder, create a file named **`.env`**
2. Put this in it, replacing `YOUR_KEY_HERE` with your key from Step 1:

**If you used Groq:**
```
GROQ_API_KEY=YOUR_KEY_HERE
```

**If you used Gemini:**
```
GEMINI_API_KEY=YOUR_KEY_HERE
```

Example:
```
GROQ_API_KEY=gsk_abc123xyz...
```

Save the file.

---

## Step 6: First Run — Connect Gmail (5 minutes)

In the same folder, run:

```bash
python run.py --initial
```

(or `python3 run.py --initial`)

1. A browser will open for Google sign-in
2. Choose the Gmail account you use for job applications
3. If you see “Google hasn’t verified this app”, click **Advanced** → **Go to Job Tracker (unsafe)**
4. Click **Allow**
5. The script will run and scan your last 8 months of Gmail

You’ll see something like:

```
Loaded SPREADSHEET_ID: ...
Found X emails to skip forever
Found X emails to retry from previous run
...
--- Done ---
Google Sheet: https://docs.google.com/spreadsheets/d/...
```

6. A file **`token.json`** will appear in the project folder — do not delete it.
7. Copy the **Spreadsheet ID** from the printed URL (the long string between `/d/` and the next `/`).

Example URL:
```
https://docs.google.com/spreadsheets/d/1a2b3c4d5e6f.../edit
```
The ID is: `1a2b3c4d5e6f...`

---

## Step 7: Add Your Spreadsheet ID to `.env`

Edit `.env` and add (with your real ID):

```
SPREADSHEET_ID=YOUR_SPREADSHEET_ID_HERE
```

So your `.env` might look like:

```
GROQ_API_KEY=gsk_abc123...
SPREADSHEET_ID=1a2b3c4d5e6f...
```

---

## Step 8: Set Up GitHub (for automatic daily runs)

1. Push this project to your GitHub:

```bash
git add .
git commit -m "Setup"
git push
```

2. In your repo on GitHub: **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret** and add:

| Name | Value |
|------|-------|
| `GROQ_API_KEY` | Your Groq key (or `GEMINI_API_KEY` if you use Gemini) |
| `GOOGLE_CREDENTIALS` | The full text inside `credentials.json` |
| `GOOGLE_TOKEN` | The full text inside `token.json` |
| `SPREADSHEET_ID` | Your spreadsheet ID |

For `GOOGLE_CREDENTIALS` and `GOOGLE_TOKEN`:
- Open each file in a text editor
- Select all and copy
- Paste into the secret value (leave everything as-is, including braces and quotes)

---

## Step 9: You’re Done

- The workflow runs daily at 8:00 AM UTC.
- You can run it anytime: **Actions** → **Daily Job Tracker Sync** → **Run workflow**
- Open your Google Sheet to see tracked applications.

---

## Quick Reference

| What | Where |
|------|-------|
| Your tracked applications | Google Sheet link printed after run |
| Run manually | `python run.py` or `python run.py --initial` |
| Check errors | `errors.log` in the project folder |
| Run from GitHub | Actions → Daily Job Tracker Sync → Run workflow |

---

## Troubleshooting

**“GEMINI_API_KEY not set” or “No GROQ_API_KEY”**  
→ Add the key to `.env` (Step 5) and run again.

**“No credentials”**  
→ Put `credentials.json` in the same folder as `run.py`.

**Browser didn’t open for sign-in**  
→ Copy the URL from the terminal and open it in your browser.

**“Invalid grant” or “Token expired”**  
→ Delete `token.json` and run `python run.py --initial` again.

**Rate limit / 429 errors**  
→ Add `GROQ_API_KEY` (Option A in Step 1). Groq allows more free requests.
