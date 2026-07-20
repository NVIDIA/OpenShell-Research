"""Pytest configuration for path setup."""

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


# Make tests reproducible by ignoring machine-specific .env config.
os.environ["REACHY_MINI_SKIP_DOTENV"] = "1"
