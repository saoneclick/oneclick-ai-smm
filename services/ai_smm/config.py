from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str
    telegram_relay_base: str
    telegram_bot_token: str
    admin_chat_id: int

    deepseek_api_key: str
    telegram_channel_id: int = 0

    deepseek_model: str = "deepseek-v4-pro"
    deepseek_base_url: str = "https://api.deepseek.com"

    cardrender_url: str = "http://cardrender:8000"
    media_base_url: str = "http://smm:8000/media"
    uploads_dir: Path = Path("/data/uploads")

    max_bot_token: str = ""
    max_chat_id: str = ""
    vk_token: str = ""
    vk_group_id: str = ""
    vk_api_version: str = "5.199"

    dzen_dir: Path = Path("/srv/dzen")
    dzen_public_base: str = "https://dzen.saoneclick.ru"

    test_token: str = ""
    timezone: str = "Asia/Yekaterinburg"
    log_level: str = "INFO"
    max_images: int = 10
    max_image_bytes: int = 12 * 1024 * 1024

    default_channels: tuple[str, ...] = field(
        default_factory=lambda: ("telegram", "max", "vk", "dzen")
    )

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = _env(
            "DATABASE_URL",
            "postgresql://postgres:postgres@postgres:5432/marketing",
        )
        admin_raw = _env("TELEGRAM_ADMIN_CHAT_ID", "0")
        try:
            admin_chat_id = int(admin_raw)
        except ValueError:
            admin_chat_id = 0
        try:
            channel_chat_id = int(_env("TELEGRAM_CHANNEL_ID", "0"))
        except ValueError:
            channel_chat_id = 0
        channels_raw = _env("SMM_DEFAULT_CHANNELS", "telegram,max,vk,dzen")
        channels = tuple(c.strip() for c in channels_raw.split(",") if c.strip())
        return cls(
            database_url=database_url,
            telegram_relay_base=_env("TELEGRAM_RELAY_BASE").rstrip("/"),
            telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
            admin_chat_id=admin_chat_id,
            telegram_channel_id=channel_chat_id,
            deepseek_api_key=_env("DEEPSEEK_API_KEY"),
            deepseek_model=_env("DEEPSEEK_MODEL", "deepseek-v4-pro"),
            deepseek_base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            cardrender_url=_env("CARD_RENDER_URL", "http://cardrender:8000").rstrip("/"),
            media_base_url=_env("MEDIA_BASE_URL", "http://smm:8000/media").rstrip("/"),
            uploads_dir=Path(_env("UPLOADS_DIR", "/data/uploads")),
            max_bot_token=_env("MAX_BOT_TOKEN"),
            max_chat_id=_env("MAX_CHAT_ID"),
            vk_token=_env("VK_TOKEN"),
            vk_group_id=_env("VK_GROUP_ID"),
            vk_api_version=_env("VK_API_VERSION", "5.199"),
            dzen_dir=Path(_env("DZEN_DIR", "/srv/dzen")),
            dzen_public_base=_env("DZEN_PUBLIC_BASE", "https://dzen.saoneclick.ru").rstrip("/"),
            test_token=_env("SMM_TEST_TOKEN"),
            timezone=_env("TIMEZONE", "Asia/Yekaterinburg"),
            log_level=_env("LOG_LEVEL", "INFO"),
            max_images=int(_env("SMM_MAX_IMAGES", "10")),
            max_image_bytes=int(_env("SMM_MAX_IMAGE_BYTES", str(12 * 1024 * 1024))),
            default_channels=channels or ("telegram",),
        )


settings = Settings.from_env()
