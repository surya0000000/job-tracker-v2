"""AI sheet cleaning - filter non-job rows and enrich Company/Role from Notes."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import json
import os
import time
from typing import Optional

import pandas as pd

# Reuse sheets auth
from src.sheets_sync import get_sheets_service
import config


PROMPT = """You are a job application classifier. The attached Excel file contains rows of email data 
auto-parsed from a Gmail inbox. Each row has a Notes column containing the actual email content or 
snippet that triggered this entry.

Your job: read the Notes column for EVERY single row and decide if it represents a REAL job application 
event — meaning the user actually applied to a job, OR received a company response to an actual 
application (confirmation, rejection, interview invite, offer, online assessment, etc).

A row is NOT a real job application if the Notes suggest it is:
- A job alert or digest email ('Here are 5 new jobs matching your search', 'New jobs for you', etc)
- A newsletter or promotional email from a job board
- Recruiter cold outreach where the user never applied ('We found your profile', 'Are you open to opportunities')
- A generic LinkedIn/Indeed/Handshake notification unrelated to a specific submitted application
- Any automated platform notification that is not a response to a specific application

Return ONLY a raw JSON object — no explanation, no markdown fences, just the JSON:
{
  "keep_rows": [0, 2, 3, 5],
  "remove_rows": [1, 4, 6],
  "reasoning": {
    "1": "job alert digest email",
    "4": "recruiter cold outreach, no application submitted",
    "6": "LinkedIn newsletter"
  }
}

Row numbers are 0-indexed and do NOT count the header row.
Process every single row — keep_rows + remove_rows must together equal the total number of data rows."""


ENRICH_PROMPT = """You are a job application data enrichment assistant. The attached Excel file 
contains job application rows that have already been confirmed as real job applications.

The "Company" and "Role" columns were auto-extracted by a rule-based system and may be inaccurate. 
The "Notes" column contains the actual email content for each row.

Your job: for EVERY row, read the Notes column and determine the most accurate Company name and 
Role/Job Title based on what the email actually says.

Rules for Company:
- Extract the actual hiring company name, not the job board (e.g. if Notes mention "Your application 
  to Google via LinkedIn", company is "Google" not "LinkedIn")
- If the email is from Greenhouse/Lever/Workday/Ashby/Taleo on behalf of a company, extract that 
  company's name
- Use the most complete, properly capitalized version of the name (e.g. "Goldman Sachs" not "goldman")
- If the existing Company value looks correct and Notes confirm it, keep it as-is
- If truly unidentifiable from Notes, keep the existing value

Rules for Role:
- Extract the full job title including seniority, specialization, and team if mentioned
  (e.g. "Software Engineer II, Payments Infrastructure" instead of just "Software Engineer")
- Include intern/co-op/contract/full-time qualifier if present in the email
- Use the exact title from the email when possible, not a paraphrase
- If the existing Role value looks correct and Notes confirm it, keep it as-is
- If truly unidentifiable from Notes, keep the existing value

Return ONLY a raw JSON object — no explanation, no markdown fences, just the JSON:
{
  "enriched": {
    "0": {"company": "Google", "role": "Software Engineer Intern, Core Systems"},
    "1": {"company": "Goldman Sachs", "role": "Summer Analyst, Technology Division"},
    "3": {"company": "Stripe", "role": "New Grad Software Engineer"}
  }
}

Only include rows where you are making a change or improvement. If a row's existing Company and 
Role are already accurate based on the Notes, do not include that row index in the response.
Row numbers are 0-indexed and do NOT count the header row."""


def _ensure_tab_exists(spreadsheet_id: str, tab_name: str) -> None:
    """Create tab if it doesn't exist."""
    service = get_sheets_service()
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if tab_name not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()


def read_applications_tab(spreadsheet_id: str) -> pd.DataFrame:
    """Read entire Applications tab into DataFrame."""
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="Applications!A:G",
    ).execute()
    values = result.get("values", [])
    if not values:
        return pd.DataFrame(columns=["Company", "Role", "Stage", "Type", "Date Applied", "Last Updated", "Notes"])
    STANDARD_COLS = ["Company", "Role", "Stage", "Type", "Date Applied", "Last Updated", "Notes"]
    raw_headers = values[0]
    if len(raw_headers) < 7:
        raw_headers = raw_headers + [""] * (7 - len(raw_headers))
    headers = STANDARD_COLS
    rows = []
    for row in values[1:]:
        r = row + [""] * (7 - len(row)) if len(row) < 7 else row[:7]
        rows.append(r)
    df = pd.DataFrame(rows, columns=headers)
    return df


def gemini_clean(df: pd.DataFrame, xlsx_path: str) -> dict:
    """Run Gemini classification on xlsx, return {keep_rows, remove_rows, reasoning}."""
    import google.generativeai as genai
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")
    genai.configure(api_key=api_key)

    uploaded_file = genai.upload_file(
        path=xlsx_path,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    while uploaded_file.state.name == "PROCESSING":
        time.sleep(2)
        uploaded_file = genai.get_file(uploaded_file.name)

    if uploaded_file.state.name != "ACTIVE":
        raise Exception(f"Gemini file upload failed: {uploaded_file.state.name}")

    model = genai.GenerativeModel("gemini-2.0-flash")
    response = model.generate_content([uploaded_file, PROMPT])
    raw = response.text

    genai.delete_file(uploaded_file.name)

    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    result = json.loads(raw)
    return result


def chatgpt_clean(df: pd.DataFrame, xlsx_path: str) -> dict:
    """Run ChatGPT classification via Assistants API, return {keep_rows, remove_rows, reasoning}."""
    import openai
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set")
    client = openai.OpenAI(api_key=api_key)

    with open(xlsx_path, "rb") as f:
        uploaded_file = client.files.create(file=f, purpose="assistants")

    assistant = client.beta.assistants.create(
        name="Job App Cleaner",
        instructions="You are a job application classifier. Analyze Excel files and return JSON only.",
        model="gpt-4o",
        tools=[{"type": "code_interpreter"}],
    )

    thread = client.beta.threads.create()
    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=PROMPT,
        attachments=[{"file_id": uploaded_file.id, "tools": [{"type": "code_interpreter"}]}],
    )

    run = client.beta.threads.runs.create_and_poll(
        thread_id=thread.id,
        assistant_id=assistant.id,
        timeout=300,
    )

    if run.status != "completed":
        raise Exception(f"ChatGPT run failed with status: {run.status}")

    messages = client.beta.threads.messages.list(thread_id=thread.id)
    raw = messages.data[0].content[0].text.value

    client.files.delete(uploaded_file.id)
    client.beta.assistants.delete(assistant.id)

    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    result = json.loads(raw)
    return result


def apply_filter(df: pd.DataFrame, result: dict, model_name: str) -> tuple[pd.DataFrame, list[dict]]:
    """Split df into kept_df and removed_df. Return kept_df with Removal_Reason column + removed_summary."""
    keep_rows = set(result.get("keep_rows", []))
    remove_rows = set(result.get("remove_rows", []))
    reasoning = result.get("reasoning", {})

    kept_indices = [i for i in range(len(df)) if i in keep_rows]
    kept_df = df.iloc[kept_indices].copy()
    kept_df["Removal_Reason"] = ""

    removed_summary = []
    for i in sorted(remove_rows):
        if i < len(df):
            row = df.iloc[i]
            reason = reasoning.get(str(i), "No reason provided")
            removed_summary.append({
                "row": i,
                "company": str(row.get("Company", ""))[:50],
                "role": str(row.get("Role", ""))[:50],
                "reason": reason,
            })

    return kept_df, removed_summary


def enrich_company_role(df: pd.DataFrame, xlsx_path: str, model_name: str) -> tuple[pd.DataFrame, int]:
    """Enrich Company and Role from Notes. Returns (enriched_df, count_enriched)."""
    if model_name == "gemini":
        return _enrich_gemini(df, xlsx_path)
    return _enrich_chatgpt(df, xlsx_path)


def _enrich_gemini(df: pd.DataFrame, xlsx_path: str) -> tuple[pd.DataFrame, int]:
    import google.generativeai as genai
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return df, 0
    genai.configure(api_key=api_key)

    uploaded_file = genai.upload_file(
        path=xlsx_path,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    while uploaded_file.state.name == "PROCESSING":
        time.sleep(2)
        uploaded_file = genai.get_file(uploaded_file.name)
    if uploaded_file.state.name != "ACTIVE":
        return df, 0

    model = genai.GenerativeModel("gemini-2.0-flash")
    response = model.generate_content([uploaded_file, ENRICH_PROMPT])
    genai.delete_file(uploaded_file.name)

    raw = response.text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    result = json.loads(raw)
    enriched_data = result.get("enriched", {})
    enriched_rows = set()
    for row_idx_str, updates in enriched_data.items():
        try:
            idx = int(row_idx_str)
            if 0 <= idx < len(df):
                if "company" in updates and updates["company"]:
                    df.iloc[idx, df.columns.get_loc("Company")] = updates["company"]
                    enriched_rows.add(idx)
                if "role" in updates and updates["role"]:
                    df.iloc[idx, df.columns.get_loc("Role")] = updates["role"]
                    enriched_rows.add(idx)
        except Exception:
            pass
    return df, len(enriched_rows)


def _enrich_chatgpt(df: pd.DataFrame, xlsx_path: str) -> tuple[pd.DataFrame, int]:
    import openai
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return df, 0
    client = openai.OpenAI(api_key=api_key)

    with open(xlsx_path, "rb") as f:
        uploaded_file = client.files.create(file=f, purpose="assistants")

    assistant = client.beta.assistants.create(
        name="Job App Enricher",
        instructions="Enrich job application data. Return JSON only.",
        model="gpt-4o",
        tools=[{"type": "code_interpreter"}],
    )

    thread = client.beta.threads.create()
    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=ENRICH_PROMPT,
        attachments=[{"file_id": uploaded_file.id, "tools": [{"type": "code_interpreter"}]}],
    )

    run = client.beta.threads.runs.create_and_poll(
        thread_id=thread.id,
        assistant_id=assistant.id,
        timeout=300,
    )

    client.files.delete(uploaded_file.id)
    client.beta.assistants.delete(assistant.id)

    if run.status != "completed":
        return df, 0

    messages = client.beta.threads.messages.list(thread_id=thread.id)
    raw = messages.data[0].content[0].text.value
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    result = json.loads(raw)
    enriched_data = result.get("enriched", {})
    enriched_rows = set()
    for row_idx_str, updates in enriched_data.items():
        try:
            idx = int(row_idx_str)
            if 0 <= idx < len(df):
                if "company" in updates and updates["company"]:
                    df.iloc[idx, df.columns.get_loc("Company")] = updates["company"]
                    enriched_rows.add(idx)
                if "role" in updates and updates["role"]:
                    df.iloc[idx, df.columns.get_loc("Role")] = updates["role"]
                    enriched_rows.add(idx)
        except Exception:
            pass
    return df, len(enriched_rows)


def write_tab(spreadsheet_id: str, tab_name: str, df: pd.DataFrame) -> None:
    """Write DataFrame to tab. Create tab if missing, clear, write header + rows."""
    _ensure_tab_exists(spreadsheet_id, tab_name)
    service = get_sheets_service()

    cols = list(df.columns)
    rows = [cols]
    for _, r in df.iterrows():
        rows.append([str(r.get(c, "")) for c in cols])

    range_name = f"'{tab_name}'!A1"
    clear_range = f"'{tab_name}'!A:Z"
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=clear_range,
    ).execute()
    if rows:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()
    print(f"Written {len(rows) - 1} rows to tab '{tab_name}'")


def run_ai_cleaning(
    gemini_only: bool = False,
    chatgpt_only: bool = False,
) -> None:
    """Main entry: read Applications, filter with AI, enrich, write to Cleaned_Gemini and Cleaned_ChatGPT tabs."""
    spreadsheet_id = config.get_spreadsheet_id()
    if not spreadsheet_id:
        raise ValueError("SPREADSHEET_ID not set. Add it to .env or set as GitHub secret.")

    print("Reading Applications tab...")
    df = read_applications_tab(spreadsheet_id)
    total_rows = len(df)

    if total_rows == 0:
        print("Applications tab is empty. Nothing to clean.")
        return

    xlsx_full = "/tmp/applications_full.xlsx"
    df.to_excel(xlsx_full, index=False)
    print(f"Saved {total_rows} rows to {xlsx_full}")

    gemini_kept = gemini_removed = 0
    chatgpt_kept = chatgpt_removed = 0
    gemini_enriched = chatgpt_enriched = 0

    if not chatgpt_only:
        try:
            print("Running Gemini classification...")
            result = gemini_clean(df, xlsx_full)
            kept_df, removed = apply_filter(df, result, "gemini")
            gemini_kept = len(kept_df)
            gemini_removed = len(removed)

            kept_df = kept_df.reset_index(drop=True)
            kept_df.to_excel("/tmp/applications_cleaned_gemini.xlsx", index=False)
            try:
                enriched_df, count = enrich_company_role(kept_df, "/tmp/applications_cleaned_gemini.xlsx", "gemini")
                gemini_enriched = count
                kept_df = enriched_df
                if count > 0:
                    print(f"Gemini enriched {count} rows (company/role improved from Notes content)")
            except Exception as e:
                print(f"Gemini enrichment failed: {e}")

            write_tab(spreadsheet_id, "Cleaned_Gemini", kept_df)
        except Exception as e:
            print(f"Gemini cleaning failed: {e}")

    if not gemini_only:
        try:
            print("Running ChatGPT classification...")
            result = chatgpt_clean(df, xlsx_full)
            kept_df, removed = apply_filter(df, result, "chatgpt")
            chatgpt_kept = len(kept_df)
            chatgpt_removed = len(removed)

            kept_df = kept_df.reset_index(drop=True)
            kept_df.to_excel("/tmp/applications_cleaned_chatgpt.xlsx", index=False)
            try:
                enriched_df, count = enrich_company_role(kept_df, "/tmp/applications_cleaned_chatgpt.xlsx", "chatgpt")
                chatgpt_enriched = count
                kept_df = enriched_df
                if count > 0:
                    print(f"ChatGPT enriched {count} rows (company/role improved from Notes content)")
            except Exception as e:
                print(f"ChatGPT enrichment failed: {e}")

            write_tab(spreadsheet_id, "Cleaned_ChatGPT", kept_df)
        except Exception as e:
            print(f"ChatGPT cleaning failed: {e}")

    print("\n--- Summary ---")
    print(f"Applications tab total: {total_rows} rows")
    if not chatgpt_only:
        print(f"Gemini  → kept {gemini_kept}, removed {gemini_removed}")
    if not gemini_only:
        print(f"ChatGPT → kept {chatgpt_kept}, removed {chatgpt_removed}")
    print("Both tabs updated in Google Sheet.")

    print("\nNOTE: Make sure OPENAI_API_KEY is set in your .env for local runs,")
    print("and added as a GitHub Actions secret named OPENAI_API_KEY for CI runs.")


if __name__ == "__main__":
    run_ai_cleaning()
