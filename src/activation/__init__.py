# -*- coding: utf-8 -*-
"""Activation module: identity, OTA, HTTP client and UI factory."""

from .factory import create_activation_ui
from .service import ActivationService

__all__ = ["ActivationService", "create_activation_ui"]
