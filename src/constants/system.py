# System constants definition
from enum import Enum


class InitializationStage(Enum):
    """
    Initialization phase enumeration.
    """

    DEVICE_FINGERPRINT = "First phase: device identity preparation"
    CONFIG_MANAGEMENT = "Second phase: configuration management initialization"
    OTA_CONFIG = "Third phase: fetch OTA config"
    ACTIVATION = "Fourth phase: activation flow"


class SystemConstants:
    """
    System constants.
    """

    # Application info
    APP_NAME = "py-xiaozhi"  # Program identifier (ASCII; used for directory, configuration, bundle_id)
    APP_DISPLAY_NAME = "Xiaozhi"  # Display name (used for window title, Launchpad, installer UI)
    APP_VERSION = "2.1.2"
    BOARD_TYPE = "bread-compact-wifi"

    # Default timeout settings
    DEFAULT_TIMEOUT = 10
    ACTIVATION_MAX_RETRIES = 60
    ACTIVATION_RETRY_INTERVAL = 5

    # Filename constants
    CONFIG_FILE = "config.json"
    EFUSE_FILE = "efuse.json"
