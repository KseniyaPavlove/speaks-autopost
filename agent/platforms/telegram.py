"""Telegram-канал через Bot API (бот должен быть админом канала)."""
import json
from contextlib import ExitStack

import requests

from ..config import env

CAPTION_LIMIT = 1024
VIDEO_LIMIT = 50 * 1024 * 1024  # ограничение Bot API на загрузку


def _call(method, **kw):
    r = requests.post(f"https://api.telegram.org/bot{env('TG_BOT_TOKEN')}/{method}", timeout=600, **kw)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description')}")
    return data["result"]


def _link(message_id):
    ch = env("TG_CHANNEL")
    if ch.startswith("@"):
        return f"https://t.me/{ch[1:]}/{message_id}"
    if ch.startswith("-100"):
        return f"https://t.me/c/{ch[4:]}/{message_id}"
    return None


def publish(job):
    chat = env("TG_CHANNEL")
    text = job.caption or ""
    short = text if len(text) <= CAPTION_LIMIT else ""
    if job.format == "reels":
        if job.video_path.stat().st_size > VIDEO_LIMIT:
            raise RuntimeError("видео больше 50 МБ — Telegram-бот не может его загрузить")
        with open(job.video_path, "rb") as f:
            msg = _call("sendVideo", data={"chat_id": chat, "caption": short, "supports_streaming": "true"},
                        files={"video": f})
        first_id = msg["message_id"]
    elif len(job.image_paths) == 1:
        with open(job.image_paths[0], "rb") as f:
            msg = _call("sendPhoto", data={"chat_id": chat, "caption": short}, files={"photo": f})
        first_id = msg["message_id"]
    else:
        with ExitStack() as stack:
            files, media = {}, []
            for i, p in enumerate(job.image_paths[:10]):
                files[f"p{i}"] = stack.enter_context(open(p, "rb"))
                item = {"type": "photo", "media": f"attach://p{i}"}
                if i == 0 and short:
                    item["caption"] = short
                media.append(item)
            msgs = _call("sendMediaGroup", data={"chat_id": chat, "media": json.dumps(media)}, files=files)
        first_id = msgs[0]["message_id"]
    if text and not short:  # длинный текст — отдельным сообщением следом
        _call("sendMessage", data={"chat_id": chat, "text": text[:4096], "disable_web_page_preview": "true"})
    return {"url": _link(first_id)}


def check():
    me = _call("getMe")
    chat = _call("getChat", data={"chat_id": env("TG_CHANNEL")})
    return f"бот @{me['username']} → канал «{chat.get('title')}»"
