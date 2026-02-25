#!/usr/bin/env python3
"""Entry point for Job Application Tracker."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent / ".env")

import sys
# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from src.main import main

if __name__ == "__main__":
    main()
