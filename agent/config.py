"""Настройки агента: всё берётся из переменных окружения (GitHub Secrets / Variables)."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
BRAND_FILE = ROOT / "brand.md"


def env(name: str, default=None):
    value = os.getenv(name)
    return value.strip() if value not in (None, "") else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, default))
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "да", "on")


TZ = ZoneInfo(env("TIMEZONE", "Europe/Moscow"))

# За сколько часов до публикации присылать превью в админ-чат (0 — не присылать)
PREVIEW_HOURS = env_int("PREVIEW_HOURS", 12)
# Если пост «проспал» больше этого числа часов — не публикуем, а сообщаем
MAX_LATE_HOURS = env_int("MAX_LATE_HOURS", 6)
# Сколько раз повторять неудачную публикацию на одной площадке
MAX_ATTEMPTS = env_int("MAX_ATTEMPTS", 3)
# Публиковать только строки со статусом «готово»
REQUIRE_READY = env_bool("REQUIRE_READY", True)

PLATFORM_NAMES = {
    "ig": "Instagram",
    "tg": "Telegram",
    "vk": "VK",
    "yt": "YouTube Shorts",
    "tt": "TikTok",
}


def configured_platforms() -> set:
    """Площадки, для которых заданы ключи."""
    ok = set()
    if env("TG_BOT_TOKEN") and env("TG_CHANNEL"):
        ok.add("tg")
    if env("VK_GROUP_ID") and (env("VK_ACCESS_TOKEN") or env("VK_REFRESH_TOKEN")):
        ok.add("vk")
    if env("IG_ACCESS_TOKEN") and env("IG_USER_ID"):
        ok.add("ig")
    if env("YT_CLIENT_ID") and env("YT_CLIENT_SECRET") and env("YT_REFRESH_TOKEN"):
        ok.add("yt")
    if env("TT_CLIENT_KEY") and env("TT_CLIENT_SECRET") and env("TT_REFRESH_TOKEN"):
        ok.add("tt")
    return ok
