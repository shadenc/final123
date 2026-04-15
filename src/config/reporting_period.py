"""
Single source of truth for which **Tadawul table column year** the app uses for labels and PDF picks.

On saudiexchange.sa, each column is a fiscal/reporting year. In Jan–Apr, the newest full column is
often still the *previous* calendar year (e.g. April 2026 → column 2025 with FY2025 annual just filed).
Using datetime.now().year here wrongly labels everything as "2026" while the site still shows 2025.

Override anytime: REPORTING_FISCAL_YEAR=2026
"""

from __future__ import annotations

import os
from datetime import datetime


def _default_fiscal_column_year() -> int:
    """Match Tadawul: Jan–Apr → prior calendar year column; May–Dec → current year column."""
    now = datetime.now()
    if now.month <= 4:
        return now.year - 1
    return now.year


def reporting_fiscal_year() -> int:
    raw = os.environ.get("REPORTING_FISCAL_YEAR", "").strip()
    if raw.isdigit():
        return int(raw)
    return _default_fiscal_column_year()


def prior_annual_year() -> int:
    """Year of the annual PDF paired with Q1–Q3 flows (e.g. 2025 when focus is 2026)."""
    return reporting_fiscal_year() - 1
