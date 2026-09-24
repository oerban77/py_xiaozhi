"""Shared UI: the ViewPort contract, factory, activation base class, and cross-platform event DTOs.

The Qt ViewModel lives in src.ui.gui.models (GUI only).
"""

from src.ui.shared.activation import BaseActivation
from src.ui.shared.events import UISendTextRequest
from src.ui.shared.factory import create_viewport
from src.ui.shared.viewport import ViewPort

__all__ = [
    "BaseActivation",
    "UISendTextRequest",
    "ViewPort",
    "create_viewport",
]
