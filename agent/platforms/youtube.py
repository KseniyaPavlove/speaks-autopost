"""YouTube Shorts: вертикальное видео до 3 минут загружается как Short."""
import requests

from ..config import env


def _access_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
        "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=60)
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"YouTube: не удалось обновить токен — {data.get('error_description') or data}")
    return data["access_token"]


def publish(job):
    cap = job.caption if isinstance(job.caption, dict) else {"title": "S.peaks #shorts", "description": job.caption or ""}
    privacy = env("YT_PRIVACY", "public")
    tok = _access_token()
    size = job.video_path.stat().st_size
    body = {
        "snippet": {"title": cap["title"], "description": cap["description"],
                    "categoryId": env("YT_CATEGORY", "27"),  # 27 = Образование
                    "defaultLanguage": "ru"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/*", "X-Upload-Content-Length": str(size)},
        json=body, timeout=120)
    if init.status_code >= 400:
        raise RuntimeError(f"YouTube: {init.text[:400]}")
    with open(job.video_path, "rb") as f:
        r = requests.put(init.headers["Location"], headers={"Authorization": f"Bearer {tok}",
                                                            "Content-Type": "video/*"}, data=f, timeout=3600)
    if r.status_code >= 400:
        raise RuntimeError(f"YouTube upload: {r.text[:400]}")
    video = r.json()
    res = {"url": f"https://youtube.com/shorts/{video['id']}"}
    got = video.get("status", {}).get("privacyStatus")
    if got and got != privacy:
        res["note"] = f"YouTube поставил видео как {got} — проект Google ещё не прошёл проверку, откройте вручную"
    return res


def check():
    tok = _access_token()
    r = requests.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                     headers={"Authorization": f"Bearer {tok}"}, timeout=60).json()
    items = r.get("items") or []
    return f"канал «{items[0]['snippet']['title']}»" if items else f"канал не найден: {r}"
