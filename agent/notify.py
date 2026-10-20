"""Сообщения в админ-чат Telegram: превью, отчёты, ошибки."""
import requests

from .config import env


def _api(method, **kw):
    token = env("TG_BOT_TOKEN")
    return requests.post(f"https://api.telegram.org/bot{token}/{method}", timeout=120, **kw)


def admin(text: str, photo_path=None):
    chat = env("ADMIN_CHAT_ID")
    print(text)
    if not (chat and env("TG_BOT_TOKEN")):
        return
    try:
        if photo_path:
            with open(photo_path, "rb") as f:
                _api("sendPhoto", data={"chat_id": chat, "caption": text[:1024]}, files={"photo": f})
            if len(text) > 1024:
                _send_long(chat, text[1024:])
        else:
            _send_long(chat, text)
    except Exception as e:
        print(f"не удалось отправить уведомление: {e}")


def _send_long(chat, text):
    for i in range(0, len(text), 4000):
        _api("sendMessage", data={"chat_id": chat, "text": text[i:i + 4000],
                                  "disable_web_page_preview": "true"})
