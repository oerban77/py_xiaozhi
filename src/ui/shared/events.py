"""Data classes for UI-related events."""

from dataclasses import dataclass


@dataclass
class UISendTextRequest:
    """Send text."""

    text: str
