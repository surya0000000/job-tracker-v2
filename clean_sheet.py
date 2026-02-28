#!/usr/bin/env python3
"""Clean job application sheet using AI models (Gemini, ChatGPT, Groq, Grok)."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent / ".env")

import argparse
from src.ai_cleaner import run_ai_cleaning

parser = argparse.ArgumentParser(description="Clean job application sheet using AI models")
parser.add_argument("--gemini-only", action="store_true", help="Run only Gemini")
parser.add_argument("--chatgpt-only", action="store_true", help="Run only ChatGPT")
parser.add_argument("--groq-only", action="store_true", help="Run only Groq")
parser.add_argument("--grok-only", action="store_true", help="Run only Grok")
args = parser.parse_args()

run_ai_cleaning(
    gemini_only=args.gemini_only,
    chatgpt_only=args.chatgpt_only,
    groq_only=args.groq_only,
    grok_only=args.grok_only,
)
