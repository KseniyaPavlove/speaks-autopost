"""Публикаторы площадок. У каждого функция publish(job) -> {"url": ..., "note": ...}."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Job:
    post_id: str
    format: str                     # post | carousel | reels | story
    caption: object                 # str, а для YouTube — {"title", "description"}
    image_urls: list = field(default_factory=list)   # публичные JPG из Figma (для Instagram)
    image_paths: list = field(default_factory=list)  # скачанные файлы (TG, VK)
    video_path: Path | None = None


def get(platform):
    from . import instagram, telegram, tiktok, vk, youtube
    return {"ig": instagram, "tg": telegram, "vk": vk, "yt": youtube, "tt": tiktok}[platform]
