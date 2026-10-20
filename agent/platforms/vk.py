"""Сообщество VK. Нужен ключ ПОЛЬЗОВАТЕЛЯ-админа: загрузка фото/видео на стену токеном сообщества недоступна."""
import secrets

import requests

from ..config import env
from ..secrets_store import persist

API = "https://api.vk.com/method/"
V = "5.199"
_token_cache = {}


def token():
    if "t" in _token_cache:
        return _token_cache["t"]
    refresh = env("VK_REFRESH_TOKEN")
    if refresh and env("VK_ID_CLIENT_ID") and env("VK_DEVICE_ID"):
        r = requests.post("https://id.vk.com/oauth2/auth", data={
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": env("VK_ID_CLIENT_ID"), "device_id": env("VK_DEVICE_ID"),
            "state": secrets.token_urlsafe(16),
        }, timeout=60)
        data = r.json()
        if "access_token" not in data:
            raise RuntimeError(f"VK ID не обновил токен: {data}")
        if data.get("refresh_token") and data["refresh_token"] != refresh:
            persist("VK_REFRESH_TOKEN", data["refresh_token"])
        _token_cache["t"] = data["access_token"]
    else:
        _token_cache["t"] = env("VK_ACCESS_TOKEN")
    return _token_cache["t"]


def call(method, **params):
    r = requests.post(API + method, data={**params, "access_token": token(), "v": V}, timeout=120)
    data = r.json()
    if "error" in data:
        e = data["error"]
        raise RuntimeError(f"VK {method}: {e.get('error_code')} {e.get('error_msg')}")
    return data["response"]


def _gid():
    return abs(int(env("VK_GROUP_ID")))


def upload_photo(path):
    srv = call("photos.getWallUploadServer", group_id=_gid())
    with open(path, "rb") as f:
        up = requests.post(srv["upload_url"], files={"photo": (path.name, f, "image/jpeg")}, timeout=300).json()
    if not up.get("photo") or up.get("photo") == "[]":
        raise RuntimeError(f"VK не принял фото: {up}")
    saved = call("photos.saveWallPhoto", group_id=_gid(), photo=up["photo"], server=up["server"], hash=up["hash"])
    return f"photo{saved[0]['owner_id']}_{saved[0]['id']}"


def upload_video(path, title, description):
    save = call("video.save", group_id=_gid(), name=title[:120], description=description[:5000], wallpost=0)
    with open(path, "rb") as f:
        up = requests.post(save["upload_url"], files={"video_file": (path.name, f, "video/mp4")}, timeout=1800)
    if up.status_code >= 400:
        raise RuntimeError(f"VK не принял видео: {up.text[:300]}")
    return f"video{save['owner_id']}_{save['video_id']}"


def publish(job):
    text = job.caption or ""
    if job.format == "reels":
        first_line = (text.strip().splitlines() or ["S.peaks"])[0]
        attachments = [upload_video(job.video_path, first_line, text)]
    else:
        attachments = [upload_photo(p) for p in job.image_paths[:10]]
    res = call("wall.post", owner_id=-_gid(), from_group=1, message=text, attachments=",".join(attachments))
    return {"url": f"https://vk.com/wall-{_gid()}_{res['post_id']}"}


def check():
    g = call("groups.getById", group_id=_gid())
    groups = g.get("groups", g) if isinstance(g, dict) else g
    return f"сообщество «{groups[0]['name']}»"
