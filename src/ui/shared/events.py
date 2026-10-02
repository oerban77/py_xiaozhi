"""Data classes for UI-related events."""

from dataclasses import dataclass


@dataclass
class UISendTextRequest:
    """Send text."""

    text: str


@dataclass
class UISendAttachmentRequest:
    """Analyze a local attachment and send its extracted content as chat text."""

    path: str
    question: str = ""
    use_document_tool: bool = False
