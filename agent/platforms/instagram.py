"""Instagram (профессиональный аккаунт, привязанный к странице Facebook) через Graph API.

Картинки Meta забирает по публичной ссылке — отдаём ссылки на JPG, которые выгрузила Figma.
Видео грузим файлом (resumable upload), поэтому хостинг не нужен.
"""
import time

import requests

from ..config import env


def _ver():
    return env("IG_API_VERSION", "v26.0")


def _base():
    return f"https://graph.facebook.com/{_ver()}"


def _req(method, path, **params):
    params["access_token"] = env("IG_ACCESS_TOKEN")
    r = requests.request(method, _base() + path, data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=120)
    data = r.json()
    if "error" in data:
        e = data["error"]
        raise RuntimeError(f"Instagram {path}: {e.get('message')} (code {e.get('code')})")
    return data


def _ig():
    return env("IG_USER_ID")


def _wait(container_id, timeout=600):
    start = time.time()
    while time.time() - start < timeout:
        st = _req("GET", f"/{container_id}", fields="status_code,status")
        code = st.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram не обработал медиа: {st.get('status')}")
        time.sleep(10)
    raise RuntimeError("Instagram слишком долго обрабатывает медиа")


def _upload_video(path, **params):
    c = _req("POST", f"/{_ig()}/media", upload_type="resumable", **params)
    size = path.stat().st_size
    with open(path, "rb") as f:
        r = requests.post(f"https://rupload.facebook.com/ig-api-upload/{_ver()}/{c['id']}",
                          headers={"Authorization": f"OAuth {env('IG_ACCESS_TOKEN')}",
                                   "offset": "0", "file_size": str(size)},
                          data=f, timeout=1800)
    if r.status_code >= 400 or not r.json().get("success", True):
        raise RuntimeError(f"Instagram не принял видео: {r.text[:300]}")
    return c["id"]


def _publish(container_id):
    _wait(container_id)
    media = _req("POST", f"/{_ig()}/media_publish", creation_id=container_id)
    try:
        link = _req("GET", f"/{media['id']}", fields="permalink").get("permalink")
    except Exception:
        link = None
    return {"url": link or f"instagram media {media['id']}"}


def publish(job):
    caption = job.caption or ""
    if job.format == "reels":
        cid = _upload_video(job.video_path, media_type="REELS", caption=caption, share_to_feed="true")
        return _publish(cid)
    if job.format == "story":
        if job.video_path:
            return _publish(_upload_video(job.video_path, media_type="STORIES"))
        res = None
        for u in job.image_urls[:10]:  # серия сторис — по кадру
            cid = _req("POST", f"/{_ig()}/media", media_type="STORIES", image_url=u)["id"]
            res = _publish(cid)
        if len(job.image_urls) > 1:
            res["note"] = f"опубликовано кадров: {min(len(job.image_urls), 10)}"
        return res
    if len(job.image_urls) == 1:
        cid = _req("POST", f"/{_ig()}/media", image_url=job.image_urls[0], caption=caption)["id"]
        return _publish(cid)
    note = "в Instagram ушли первые 10 слайдов" if len(job.image_urls) > 10 else None
    children = [_req("POST", f"/{_ig()}/media", image_url=u, is_carousel_item="true")["id"]
                for u in job.image_urls[:10]]
    for ch in children:
        _wait(ch)
    cid = _req("POST", f"/{_ig()}/media", media_type="CAROUSEL", children=",".join(children),
               caption=caption)["id"]
    res = _publish(cid)
    if note:
        res["note"] = note
    return res


def check():
    me = _req("GET", f"/{_ig()}", fields="username")
    lim = _req("GET", f"/{_ig()}/content_publishing_limit", fields="quota_usage,config")
    return f"@{me.get('username')} (использовано публикаций за сутки: {lim.get('data', [{}])[0].get('quota_usage')})"
