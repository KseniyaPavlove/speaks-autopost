"""TikTok Content Posting API (Direct Post). Пока приложение не прошло аудит TikTok,
публикации видны только автору (SELF_ONLY)."""
import math
import time

import requests

from ..config import env, env_bool
from ..secrets_store import persist

BASE = "https://open.tiktokapis.com/v2"
MB = 1024 * 1024
_token_cache = {}


def _token():
    if "t" in _token_cache:
        return _token_cache["t"]
    old = env("TT_REFRESH_TOKEN")
    r = requests.post(f"{BASE}/oauth/token/", data={
        "client_key": env("TT_CLIENT_KEY"), "client_secret": env("TT_CLIENT_SECRET"),
        "grant_type": "refresh_token", "refresh_token": old}, timeout=60)
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"TikTok: не удалось обновить токен — {data}")
    if data.get("refresh_token") and data["refresh_token"] != old:
        persist("TT_REFRESH_TOKEN", data["refresh_token"])
    _token_cache["t"] = data["access_token"]
    return _token_cache["t"]


def _post(path, payload):
    r = requests.post(f"{BASE}{path}", json=payload, timeout=120, headers={
        "Authorization": f"Bearer {_token()}", "Content-Type": "application/json; charset=UTF-8"})
    data = r.json()
    err = data.get("error", {})
    if err.get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok {path}: {err.get('code')} {err.get('message')}")
    return data.get("data", {})


def chunks(size: int):
    """Правила TikTok: куски 5–64 МБ, последний может быть больше; файл до 64 МБ — одним куском."""
    if size <= 64 * MB:
        return size, 1
    chunk = 10 * MB
    return chunk, math.floor(size / chunk)


def publish(job):
    info = _post("/post/publish/creator_info/query/", {})
    options = info.get("privacy_level_options") or ["SELF_ONLY"]
    wanted = env("TT_PRIVACY", "PUBLIC_TO_EVERYONE")
    privacy = wanted if wanted in options else ("SELF_ONLY" if "SELF_ONLY" in options else options[0])

    size = job.video_path.stat().st_size
    chunk, count = chunks(size)
    init = _post("/post/publish/video/init/", {
        "post_info": {
            "title": (job.caption or "")[:2200], "privacy_level": privacy,
            "disable_duet": False, "disable_comment": False, "disable_stitch": False,
            "brand_organic_toggle": env_bool("TT_BRAND_ORGANIC", True),  # «продвигаю свой бизнес»
        },
        "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                        "chunk_size": chunk, "total_chunk_count": count},
    })
    with open(job.video_path, "rb") as f:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            f.seek(start)
            part = f.read(end - start + 1)
            r = requests.put(init["upload_url"], data=part, timeout=1800, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(part)),
                "Content-Range": f"bytes {start}-{end}/{size}"})
            if r.status_code >= 400:
                raise RuntimeError(f"TikTok upload: {r.status_code} {r.text[:300]}")

    status = "PROCESSING"
    for _ in range(30):
        time.sleep(10)
        st = _post("/post/publish/status/fetch/", {"publish_id": init["publish_id"]})
        status = st.get("status", status)
        if status == "PUBLISH_COMPLETE":
            break
        if status == "FAILED":
            raise RuntimeError(f"TikTok отклонил видео: {st.get('fail_reason')}")
    res = {"url": f"tiktok publish_id {init['publish_id']} ({status})"}
    if privacy != wanted:
        res["note"] = f"TikTok опубликовал как {privacy} — приложение ещё не прошло аудит"
    return res


def check():
    info = _post("/post/publish/creator_info/query/", {})
    return f"@{info.get('creator_username')} (доступно: {', '.join(info.get('privacy_level_options', []))})"
