"""AI sheet cleaning - filter non-job rows and enrich Company/Role using Gemini, ChatGPT, Groq, Grok."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import os
import json
import pandas as pd
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROK_API_KEY = os.getenv("GROK_API_KEY")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")


CLASSIFY_PROMPT = """You are a job application classifier. Below is spreadsheet data 
auto-parsed from a Gmail inbox. Each row has a Notes column containing the actual email content.

Read the Notes column for EVERY single row and decide if it represents a REAL job application 
event — meaning the user actually applied to a job, OR received a company response to an actual 
application (confirmation, rejection, interview invite, offer, OA, phone screen, etc).

Mark as NOT a real job application if Notes suggest:
- A job alert or digest email ('Here are 5 new jobs matching your search', 'New jobs for you')
- A newsletter or promotional email from a job board
- Recruiter cold outreach where user never applied ('We found your profile', 'Are you open to opportunities')
- Generic LinkedIn/Indeed/Handshake notification unrelated to a specific submitted application
- Any email that is NOT about a specific application the user submitted or a response to one

Return ONLY raw JSON, absolutely no explanation, no markdown fences, just the JSON object:
{
  "keep_rows": [0, 2, 3],
  "remove_rows": [1, 4],
  "reasoning": {
    "1": "job alert digest email",
    "4": "recruiter cold outreach, user never applied"
  }
}

Row numbers are 0-indexed and do NOT count the header row.
keep_rows + remove_rows combined must equal the total number of data rows — process every row.

Here is the data:
"""

ENRICH_PROMPT = """You are a job application data enrichment assistant. Below is spreadsheet 
data of confirmed real job applications. The Company and Role columns may be inaccurate because 
they were auto-extracted by a rule-based system. The Notes column contains the actual email content.

For EVERY row, read the Notes column and determine the most accurate Company name and Role/Job Title.

Rules for Company:
- Extract the actual hiring company name, not the job board that sent the email
- If email is from Greenhouse/Lever/Workday/Ashby/Taleo on behalf of a company, extract that company
- Use properly capitalized full name ('Goldman Sachs' not 'goldman sachs')
- If the existing Company value already looks correct and Notes confirm it, keep it as-is
- If truly unidentifiable from Notes, keep the existing value

Rules for Role:
- Extract the full job title including seniority, specialization, and team name if mentioned
- Include intern/co-op/contract/full-time qualifier if present in the email
- Use the exact title from the email when possible, not a paraphrase
- Examples: 'Software Engineer II, Payments Infrastructure' not just 'Software Engineer'
- If the existing Role value already looks correct and Notes confirm it, keep it as-is
- If truly unidentifiable from Notes, keep the existing value

Return ONLY raw JSON, no explanation, no markdown fences — and ONLY include rows where you 
are actually making an improvement to Company or Role:
{
  "enriched": {
    "0": {"company": "Google", "role": "Software Engineer Intern, Core Systems"},
    "3": {"company": "Stripe", "role": "New Grad Software Engineer"}
  }
}

Row numbers are 0-indexed and do NOT count the header row.

Here is the data:
"""


def get_gspread_client():
    creds = None
    token_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "token.json")
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ])
    if not creds:
        creds_json = os.getenv("GOOGLE_TOKEN")
        if creds_json:
            import json as _json
            creds = Credentials.from_authorized_user_info(
                _json.loads(creds_json),
                ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
            )
    if not creds:
        raise ValueError("No Google credentials. Set token.json or GOOGLE_TOKEN env var.")
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return gspread.authorize(creds)


def read_applications(gc):
    print("Reading Applications tab...")
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet("Applications")
    data = ws.get_all_records()
    df = pd.DataFrame(data)
    print(f"Loaded {len(df)} rows from Applications tab")
    return df


def df_to_text(df):
    return df.to_csv(index=True)


def parse_json_response(raw):
    raw = raw.strip()
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except Exception:
                continue
    return json.loads(raw)


def gemini_clean(df):
    from google import genai
    print("Running Gemini classification...")
    client = genai.Client(api_key=GEMINI_API_KEY)
    csv_text = df_to_text(df)
    response = client.models.generate_content(
        model="gemini-2.0-flash-lite",
        contents=CLASSIFY_PROMPT + csv_text,
    )
    return parse_json_response(response.text)


def gemini_enrich(df):
    from google import genai
    print("Running Gemini enrichment...")
    client = genai.Client(api_key=GEMINI_API_KEY)
    csv_text = df_to_text(df)
    response = client.models.generate_content(
        model="gemini-2.0-flash-lite",
        contents=ENRICH_PROMPT + csv_text,
    )
    return parse_json_response(response.text)


def chatgpt_clean(df):
    import openai
    print("Running ChatGPT classification...")
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    csv_text = df_to_text(df)
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a job application classifier. Return only raw JSON, no markdown."},
            {"role": "user", "content": CLASSIFY_PROMPT + csv_text},
        ],
        max_tokens=4000,
    )
    return parse_json_response(response.choices[0].message.content)


def chatgpt_enrich(df):
    import openai
    print("Running ChatGPT enrichment...")
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    csv_text = df_to_text(df)
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a job application enrichment assistant. Return only raw JSON, no markdown."},
            {"role": "user", "content": ENRICH_PROMPT + csv_text},
        ],
        max_tokens=4000,
    )
    return parse_json_response(response.choices[0].message.content)


def groq_clean(df):
    from groq import Groq
    print("Running Groq classification...")
    client = Groq(api_key=GROQ_API_KEY)
    csv_text = df_to_text(df)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a job application classifier. Return only raw JSON, no markdown."},
            {"role": "user", "content": CLASSIFY_PROMPT + csv_text},
        ],
        max_tokens=4000,
    )
    return parse_json_response(response.choices[0].message.content)


def groq_enrich(df):
    from groq import Groq
    print("Running Groq enrichment...")
    client = Groq(api_key=GROQ_API_KEY)
    csv_text = df_to_text(df)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a job application enrichment assistant. Return only raw JSON, no markdown."},
            {"role": "user", "content": ENRICH_PROMPT + csv_text},
        ],
        max_tokens=4000,
    )
    return parse_json_response(response.choices[0].message.content)


def grok_clean(df):
    import openai
    print("Running Grok classification...")
    client = openai.OpenAI(
        api_key=GROK_API_KEY,
        base_url="https://api.x.ai/v1",
    )
    csv_text = df_to_text(df)
    response = client.chat.completions.create(
        model="grok-3-mini",
        messages=[
            {"role": "system", "content": "You are a job application classifier. Return only raw JSON, no markdown."},
            {"role": "user", "content": CLASSIFY_PROMPT + csv_text},
        ],
        max_tokens=4000,
    )
    return parse_json_response(response.choices[0].message.content)


def grok_enrich(df):
    import openai
    print("Running Grok enrichment...")
    client = openai.OpenAI(
        api_key=GROK_API_KEY,
        base_url="https://api.x.ai/v1",
    )
    csv_text = df_to_text(df)
    response = client.chat.completions.create(
        model="grok-3-mini",
        messages=[
            {"role": "system", "content": "You are a job application enrichment assistant. Return only raw JSON, no markdown."},
            {"role": "user", "content": ENRICH_PROMPT + csv_text},
        ],
        max_tokens=4000,
    )
    return parse_json_response(response.choices[0].message.content)


def apply_filter(df, result, model_name):
    keep_indices = result.get("keep_rows", [])
    remove_indices = result.get("remove_rows", [])
    reasoning = result.get("reasoning", {})

    keep_indices = [i for i in keep_indices if i < len(df)]
    remove_indices = [i for i in remove_indices if i < len(df)]

    kept_df = df.iloc[keep_indices].copy().reset_index(drop=True)
    kept_df["Removal_Reason"] = ""

    removed_df = df.iloc[remove_indices].copy().reset_index(drop=True)
    removed_df["Removal_Reason"] = [
        reasoning.get(str(i), "filtered by AI") for i in remove_indices
    ]

    print(f"{model_name} → kept {len(kept_df)}, removed {len(removed_df)}")
    for _, row in removed_df.iterrows():
        print(f"  REMOVED | {str(row.get('Company', '?'))[:25]:<25} | {str(row.get('Role', '?'))[:35]:<35} | {row['Removal_Reason']}")

    return kept_df, removed_df


def apply_enrichment(df, result, model_name):
    enriched = result.get("enriched", {})
    count = 0
    for idx_str, updates in enriched.items():
        idx = int(idx_str)
        if idx < len(df):
            if updates.get("company"):
                df.at[idx, "Company"] = updates["company"]
            if updates.get("role"):
                df.at[idx, "Role"] = updates["role"]
            count += 1
    if count > 0:
        print(f"{model_name} enriched {count} rows (company/role improved from Notes)")
    return df


def write_tab(gc, tab_name, df):
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet(tab_name)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=str(len(df) + 10), cols="20")

    headers = df.columns.tolist()
    rows = [[str(c) for c in row] for row in df.values.tolist()]
    ws.update([headers] + rows)
    print(f"Written {len(df)} rows to tab '{tab_name}'")


def run_ai_cleaning(gemini_only=False, chatgpt_only=False, groq_only=False, grok_only=False):
    if not SPREADSHEET_ID:
        raise ValueError("SPREADSHEET_ID not set. Add it to .env or set as GitHub secret.")

    gc = get_gspread_client()
    df = read_applications(gc)
    total = len(df)

    if gemini_only:
        models = ["gemini"]
    elif chatgpt_only:
        models = ["chatgpt"]
    elif groq_only:
        models = ["groq"]
    elif grok_only:
        models = ["grok"]
    else:
        models = ["gemini", "chatgpt", "groq", "grok"]

    model_config = {
        "gemini": {"clean": gemini_clean, "enrich": gemini_enrich, "tab": "Cleaned_Gemini"},
        "chatgpt": {"clean": chatgpt_clean, "enrich": chatgpt_enrich, "tab": "Cleaned_ChatGPT"},
        "groq": {"clean": groq_clean, "enrich": groq_enrich, "tab": "Cleaned_Groq"},
        "grok": {"clean": grok_clean, "enrich": grok_enrich, "tab": "Cleaned_Grok"},
    }

    summary = {}

    for model in models:
        cfg = model_config[model]
        print(f"\n{'='*50}")
        print(f"Processing model: {model.upper()}")
        print(f"{'='*50}")
        try:
            classify_result = cfg["clean"](df)
            kept_df, removed_df = apply_filter(df, classify_result, model.capitalize())

            try:
                enrich_df = kept_df.drop(columns=["Removal_Reason"], errors="ignore")
                enrich_result = cfg["enrich"](enrich_df)
                kept_df = apply_enrichment(kept_df, enrich_result, model.capitalize())
            except Exception as e:
                print(f"{model.capitalize()} enrichment failed (skipping enrichment): {e}")

            write_tab(gc, cfg["tab"], kept_df)
            summary[model] = {"kept": len(kept_df), "removed": total - len(kept_df), "status": "success"}

        except Exception as e:
            print(f"{model.capitalize()} FAILED: {e}")
            summary[model] = {"kept": 0, "removed": 0, "status": f"failed: {e}"}

    print(f"\n{'='*50}")
    print("FINAL SUMMARY")
    print(f"{'='*50}")
    print(f"Applications tab total: {total} rows\n")
    for model, s in summary.items():
        status = s["status"]
        if status == "success":
            print(f"{model.upper():<10} → kept {s['kept']}, removed {s['removed']} ✓")
        else:
            print(f"{model.upper():<10} → FAILED: {status}")
    success_tabs = [model_config[m]["tab"] for m in models if summary[m]["status"] == "success"]
    print(f"\nTabs updated: {', '.join(success_tabs)}")


if __name__ == "__main__":
    run_ai_cleaning()
