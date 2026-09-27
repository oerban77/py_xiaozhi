"""Prayer times MCP tools.

Ported from the reference Xiaozhi desktop app (mcp/mcp_jadwal_sholat.py):
- ``prayer_times_today``  — today's prayer schedule for an Indonesian city/regency
- ``prayer_times_monthly``— a full month of prayer schedules
- ``prayer_list_provinces``— list the available provinces
- ``prayer_list_cities``  — list the regencies/cities of a province

Data source: the public equran.id shalat API (https://equran.id/api/v2/shalat).
No API key is required.
"""

from __future__ import annotations

import datetime
import threading

import requests

from src.logging import get_logger

logger = get_logger()

BASE_URL = "https://equran.id/api/v2/shalat"

_TIMEOUT = 15
_MAX_MONTHLY_LINES = 40

# Local cache: the province/city lists never change, so keep them per process.
_provinsi_cache: list[str] = []
_kabkota_cache: dict[str, list[str]] = {}
_cache_lock = threading.Lock()


def _normalize(s: str) -> str:
    """Lowercase and strip the Kab./Kota/Kabupaten prefix."""
    s = (s or "").lower().strip()
    for prefix in ("kab. ", "kabupaten ", "kota "):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s.strip()


def _best_match(query: str, candidates: list[str]) -> str | None:
    """Best candidate for a query: exact → startswith → contains."""
    q = _normalize(query)
    if not q:
        return None
    for c in candidates:  # 1. exact
        if _normalize(c) == q:
            return c
    for c in candidates:  # 2. startswith
        if _normalize(c).startswith(q) or q.startswith(_normalize(c)):
            return c
    for c in candidates:  # 3. contains
        if q in _normalize(c) or _normalize(c) in q:
            return c
    return None


def _get_provinces() -> list[str]:
    with _cache_lock:
        if _provinsi_cache:
            return _provinsi_cache
    r = requests.get(f"{BASE_URL}/provinsi", timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json().get("data", [])
    with _cache_lock:
        _provinsi_cache.clear()
        _provinsi_cache.extend(data)
    return data


def _get_cities(province: str) -> list[str]:
    with _cache_lock:
        if province in _kabkota_cache:
            return _kabkota_cache[province]
    r = requests.post(
        f"{BASE_URL}/kabkota", json={"provinsi": province}, timeout=_TIMEOUT
    )
    r.raise_for_status()
    data = r.json().get("data", [])
    with _cache_lock:
        _kabkota_cache[province] = data
    return data


def _resolve_location(province_in: str, city_in: str) -> tuple[str, str]:
    """Resolve the province and city names with fuzzy matching.

    Raises ValueError when the location cannot be found.
    """
    provinces = _get_provinces()
    province = _best_match(province_in, provinces)
    if not province:
        raise ValueError(
            f"Province '{province_in}' not found.\n"
            f"Available provinces: {', '.join(provinces)}"
        )

    cities = _get_cities(province)
    city = _best_match(city_in, cities)
    if not city:
        raise ValueError(
            f"City/regency '{city_in}' not found in {province}.\n"
            f"Available: {', '.join(cities)}"
        )
    return province, city


def _get_schedule(
    province: str, city: str, month: int | None = None, year: int | None = None
) -> dict:
    now = datetime.datetime.now()
    body = {
        "provinsi": province,
        "kabkota": city,
        "bulan": month or now.month,
        "tahun": year or now.year,
    }
    r = requests.post(BASE_URL, json=body, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 200:
        raise ValueError(f"API error: {data.get('message')}")
    return data.get("data", {})


def _get_today(province: str, city: str) -> tuple[dict | None, dict]:
    now = datetime.datetime.now()
    data = _get_schedule(province, city, now.month, now.year)
    for item in data.get("jadwal", []):
        if item.get("tanggal") == now.day:
            return item, data
    return None, data


async def prayer_times_today(args: dict) -> str:
    """Today's prayer times for a city/regency in Indonesia."""
    province_in = (args.get("province") or "").strip()
    city_in = (args.get("city") or "").strip()
    if not province_in or not city_in:
        return "Error: province and city are required."

    try:
        province, city = _resolve_location(province_in, city_in)
    except ValueError as e:
        return str(e)
    except requests.exceptions.RequestException as e:
        return f"Error fetching data from the API: {e}"

    try:
        item, _ = _get_today(province, city)
    except requests.exceptions.RequestException as e:
        return f"Error fetching data from the API: {e}"
    except ValueError as e:
        return f"Error: {e}"

    if not item:
        return f"No prayer schedule found for {city} today."

    return (
        f"Prayer times for {city}, {province}\n"
        f"Date: {item.get('hari')}, {item.get('tanggal_lengkap')}\n\n"
        f"Imsak  : {item.get('imsak')}\n"
        f"Fajr   : {item.get('subuh')}\n"
        f"Sunrise: {item.get('terbit')}\n"
        f"Dhuha  : {item.get('dhuha')}\n"
        f"Dhuhr  : {item.get('dzuhur')}\n"
        f"Asr    : {item.get('ashar')}\n"
        f"Maghrib: {item.get('maghrib')}\n"
        f"Isha   : {item.get('isya')}"
    )


async def prayer_times_monthly(args: dict) -> str:
    """A full month of prayer times for a city/regency in Indonesia."""
    province_in = (args.get("province") or "").strip()
    city_in = (args.get("city") or "").strip()
    if not province_in or not city_in:
        return "Error: province and city are required."

    month = args.get("month")
    year = args.get("year")
    try:
        month = int(month) if month not in (None, "") else None
        year = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        return "Error: month and year must be integers."

    try:
        province, city = _resolve_location(province_in, city_in)
    except ValueError as e:
        return str(e)
    except requests.exceptions.RequestException as e:
        return f"Error fetching data from the API: {e}"

    try:
        data = _get_schedule(province, city, month, year)
    except requests.exceptions.RequestException as e:
        return f"Error fetching data from the API: {e}"
    except ValueError as e:
        return f"Error: {e}"

    schedule = data.get("jadwal", [])
    if not schedule:
        return f"No prayer schedule found for {city}."

    lines = [
        f"Prayer times for {city}, {province} — "
        f"{data.get('bulan_nama', '')} {data.get('tahun')}"
    ]
    for item in schedule[:_MAX_MONTHLY_LINES]:
        lines.append(
            f"{item['tanggal']:2d} {item['hari'][:3]}: "
            f"Fajr {item['subuh']} | Dhuhr {item['dzuhur']} | "
            f"Asr {item['ashar']} | Maghrib {item['maghrib']} | Isha {item['isya']}"
        )
    if len(schedule) > _MAX_MONTHLY_LINES:
        lines.append(f"... ({len(schedule) - _MAX_MONTHLY_LINES} more days omitted)")
    return "\n".join(lines)


async def prayer_list_provinces(args: dict) -> str:
    """List every province available in the prayer-times API."""
    try:
        provinces = _get_provinces()
    except requests.exceptions.RequestException as e:
        return f"Error fetching data from the API: {e}"
    if not provinces:
        return "No provinces available."
    return "Provinces:\n" + "\n".join(f"- {p}" for p in provinces)


async def prayer_list_cities(args: dict) -> str:
    """List every regency/city of a province."""
    province_in = (args.get("province") or "").strip()
    if not province_in:
        return "Error: province is required."

    try:
        provinces = _get_provinces()
        province = _best_match(province_in, provinces) or province_in
        cities = _get_cities(province)
    except requests.exceptions.RequestException as e:
        return f"Error fetching data from the API: {e}"

    if not cities:
        return f"No cities found for province '{province}'."

    lines = [f"Regencies/cities in {province}:"]
    kota = [c for c in cities if c.startswith("Kota")]
    kab = [c for c in cities if c.startswith("Kab.")]
    if kota:
        lines.append("\nCities:")
        lines.extend(f"  - {c}" for c in kota)
    if kab:
        lines.append("\nRegencies:")
        lines.extend(f"  - {c}" for c in kab)
    return "\n".join(lines)
