"""Indonesia national holiday MCP tools.

Data source: https://api.kemendesa.link/libur-nasional
Single tool (self.indonesia_holiday_query) with three actions:
  - list  : holidays of the current/latest year
  - year  : holidays of a given year
  - check : is a specific date a holiday?

Reference: xiaozhi-esp32/main/holiday_mcp_tools.cc
"""

from __future__ import annotations

import datetime
import re
from typing import Any

import requests

from src.logging import get_logger

logger = get_logger()

_BASE_URL = "https://api.kemendesa.link/libur-nasional"
_TIMEOUT = 15
_MAX_OUTPUT = 16_000


def _http_get(path: str) -> dict[str, Any]:
    """GET the API and return the parsed JSON. Raises on failure."""
    url = f"{_BASE_URL}{path}"
    r = requests.get(url, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _validate_date(date: str) -> bool:
    """Check YYYY-MM-DD format and basic validity."""
    if not date or len(date) != 10:
        return False
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return False
    try:
        datetime.datetime.strptime(date, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _action_list() -> str:
    """Return all holidays of the current/latest year."""
    data = _http_get("/api/holidays/latest")
    holidays = data.get("data", [])
    if not holidays:
        return "Tidak ada data libur nasional untuk tahun ini."

    lines = [f"Daftar libur nasional tahun {datetime.datetime.now().year}:", ""]
    for h in holidays:
        date = h.get("date", "?")
        name = h.get("name", "?")
        flags = []
        if h.get("is_civic"):
            flags.append("sibik")
        if h.get("is_religious"):
            flags.append("religious")
        if h.get("is_cuti_bersama"):
            flags.append("cuti bersama")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"  {date}  {name}{flag_str}")

    output = "\n".join(lines)
    if len(output) > _MAX_OUTPUT:
        output = output[:_MAX_OUTPUT] + f"\n... (dipotong, total {len(output)} karakter)"
    return output


def _action_year(year: int) -> str:
    """Return all holidays of a given year."""
    if year < 2000 or year > 2100:
        return f"Parameter year tidak valid ({year}). Gunakan tahun antara 2000-2100."

    data = _http_get(f"/api/holidays/{year}.json")
    holidays = data.get("data", [])
    if not holidays:
        return f"Tidak ada data libur nasional untuk tahun {year}."

    lines = [f"Daftar libur nasional tahun {year}:", ""]
    for h in holidays:
        date = h.get("date", "?")
        name = h.get("name", "?")
        flags = []
        if h.get("is_civic"):
            flags.append("sibik")
        if h.get("is_religious"):
            flags.append("religious")
        if h.get("is_cuti_bersama"):
            flags.append("cuti bersama")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"  {date}  {name}{flag_str}")

    output = "\n".join(lines)
    if len(output) > _MAX_OUTPUT:
        output = output[:_MAX_OUTPUT] + f"\n... (dipotong, total {len(output)} karakter)"
    return output


def _action_check(date: str) -> str:
    """Check if a specific date is a holiday."""
    data = _http_get(f"/api/is-holiday?date={date}")
    is_holiday = data.get("is_holiday", False)
    if not is_holiday:
        return f"{date} bukan hari libur nasional."

    h = data.get("data", {})
    name = h.get("name", "?")
    flags = []
    if h.get("is_civic"):
        flags.append("sibik")
    if h.get("is_religious"):
        flags.append("religious")
    if h.get("is_cuti_bersama"):
        flags.append("cuti bersama")
    flag_str = f" [{', '.join(flags)}]" if flags else ""
    return f"{date} adalah hari libur: {name}{flag_str}"


def indonesia_holiday_query(args: dict[str, Any]) -> str:
    """Main entry point for self.indonesia_holiday_query.

    Actions:
      - list  : holidays of the current/latest year (default)
      - year  : holidays of a given year (param: year, e.g. 2026)
      - check : is a specific date a holiday? (param: date, YYYY-MM-DD)
    """
    action = str(args.get("action") or "list").strip().lower()
    year = args.get("year")
    date = str(args.get("date") or "").strip()

    if action == "list":
        return _action_list()

    if action == "year":
        try:
            year_val = int(year) if year else 0
        except (TypeError, ValueError):
            return "Parameter year harus berupa angka, contoh: 2026."
        return _action_year(year_val)

    if action == "check":
        if not _validate_date(date):
            return (
                "Parameter date tidak valid. Format: YYYY-MM-DD, contoh: 2026-08-17."
            )
        return _action_check(date)

    return (
        f"Action '{action}' tidak dikenal. Gunakan: "
        "'list' (default), 'year' (param: year), atau 'check' (param: date YYYY-MM-DD)."
    )
