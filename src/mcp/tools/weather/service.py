"""Weather data (currently a mock; a real API is pending)."""

from __future__ import annotations

import json
from typing import Any

from src.logging import get_logger

logger = get_logger()


def get_weather_payload(args: dict[str, Any]) -> str:
    city = args.get("city", "Beijing")
    logger.info(f"[WeatherTool] Getting {city} current weather for")
    # TODO: in the real project this should call a weather API
    weather_data = {
        "city": city,
        "temperature": 25,
        "condition": "Clear",
        "humidity": 45,
        "wind": "Northeast wind, force 3",
        "aqi": 52,
    }
    return json.dumps(weather_data, ensure_ascii=False)


def get_forecast_payload(args: dict[str, Any]) -> str:
    city = args.get("city", "Beijing")
    days = args.get("days", 3)
    logger.info(f"[WeatherTool] Getting {city}-day weather forecast for {days}")
    forecast = [
        {"date": "Today", "high": 28, "low": 18, "condition": "Clear"},
        {"date": "Tomorrow", "high": 26, "low": 17, "condition": "Cloudy"},
        {"date": "Day after tomorrow", "high": 24, "low": 15, "condition": "Light rain"},
    ]
    return json.dumps({"city": city, "forecast": forecast[:days]}, ensure_ascii=False)
