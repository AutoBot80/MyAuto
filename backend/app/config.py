import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Paths (injectable for tests / different envs)
APP_ROOT = Path(__file__).resolve().parents[1]
UPLOADS_DIR = APP_ROOT.parent / "Uploaded scans"

