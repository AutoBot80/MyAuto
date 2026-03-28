"""
Backward-compatible import shim.

Fill DMS implementation has moved to `fill_hero_dms_service.py`.
"""

from app.services.fill_hero_dms_service import *  # noqa: F401,F403
from app.services.fill_hero_insurance_service import run_fill_insurance_only  # noqa: F401

