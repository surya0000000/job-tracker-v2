#!/usr/bin/env python3
"""Thin wrapper for AI sheet cleaning. Run with --gemini-only or --chatgpt-only to skip one model."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent / ".env")

import argparse

from src.ai_cleaner import run_ai_cleaning

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gemini-only", action="store_true", help="Run only Gemini cleaning")
    parser.add_argument("--chatgpt-only", action="store_true", help="Run only ChatGPT cleaning")
    args = parser.parse_args()

    run_ai_cleaning(gemini_only=args.gemini_only, chatgpt_only=args.chatgpt_only)
