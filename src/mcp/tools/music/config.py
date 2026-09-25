"""Music-related configuration reading."""

from src.logging import get_logger

logger = get_logger()

DEFAULT_SEARCH_URL = "http://search.kuwo.cn/r.s"
DEFAULT_URL_API = "https://lxmusicapi.onrender.com"
DEFAULT_URL_API_KEY = "share-v3"
DEFAULT_LYRICS_URL = "http://m.kuwo.cn/newh5/singles/songinfoandlrc"
DEFAULT_OPUS_CATALOG_URL = (
    "https://cdn.jsdelivr.net/gh/oerban77/music_opus@main/catalog.json"
)
DEFAULT_OPUS_STREAM_BASE = (
    "https://cdn.jsdelivr.net/gh/oerban77/music_opus@main/music/"
)


def _cfg_str(cm, path: str, default: str) -> str:
    # The settings page allows leaving it blank to use the default; get_config does not fall back to the default for ""
    value = cm.get_config(path, default)
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def load_music_config() -> dict:
    from src.utils.config_manager import get_config

    cm = get_config()
    pick = _cfg_str

    return {
        "SEARCH_URL": pick(cm, "MUSIC.SEARCH_URL", DEFAULT_SEARCH_URL),
        "URL_API": pick(cm, "MUSIC.URL_API", DEFAULT_URL_API),
        "URL_API_KEY": pick(cm, "MUSIC.URL_API_KEY", DEFAULT_URL_API_KEY),
        "LYRICS_URL": pick(cm, "MUSIC.LYRICS_URL", DEFAULT_LYRICS_URL),
        "DEFAULT_SOURCE": pick(cm, "MUSIC.DEFAULT_PLATFORM", "kw") or "kw",
        "DEFAULT_BR": pick(cm, "MUSIC.DEFAULT_QUALITY", "320k") or "320k",
        "OPUS_CATALOG_URL": pick(
            cm, "MUSIC.OPUS_CATALOG_URL", DEFAULT_OPUS_CATALOG_URL
        ),
        "OPUS_STREAM_BASE": pick(
            cm, "MUSIC.OPUS_STREAM_BASE", DEFAULT_OPUS_STREAM_BASE
        ),
        "SEARCH_LIMIT": 20,
        "HEADERS": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        },
    }
