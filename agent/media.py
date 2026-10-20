"""Достаём картинки из Figma и видео по ссылке (Google Drive или прямая ссылка)."""
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .config import env

FIGMA_API = "https://api.figma.com/v1"
CONTAINER_TYPES = {"SECTION", "GROUP"}
SLIDE_TYPES = {"FRAME", "COMPONENT", "INSTANCE"}


def parse_figma(link: str):
    """https://www.figma.com/design/KEY/Name?node-id=12-34 -> ("KEY", "12:34")."""
    u = urlparse(link)
    m = re.match(r"/(?:design|file|proto)/([A-Za-z0-9]+)(?:/branch/([A-Za-z0-9]+))?", u.path)
    if not m:
        raise ValueError(f"не похоже на ссылку Figma: {link}")
    key = m.group(2) or m.group(1)
    node = parse_qs(u.query).get("node-id", [None])[0]
    if not node:
        raise ValueError(f"в ссылке нет node-id (скопируйте ссылку на конкретный фрейм): {link}")
    return key, unquote(node).replace("-", ":")


class Figma:
    def __init__(self, token: str | None = None, scale: float | None = None):
        self.token = token or env("FIGMA_TOKEN")
        self.scale = scale or float(env("FIGMA_SCALE", "1"))
        if not self.token:
            raise RuntimeError("не задан FIGMA_TOKEN")

    def _get(self, path, **params):
        for attempt in range(4):
            r = requests.get(f"{FIGMA_API}{path}", params=params,
                             headers={"X-Figma-Token": self.token}, timeout=120)
            if r.status_code == 429 and attempt < 3:
                time.sleep(int(r.headers.get("Retry-After", 20)))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Figma API {r.status_code}: {r.text[:300]}")
            return r.json()

    def _children_frames(self, key, node_id):
        data = self._get(f"/files/{key}/nodes", ids=node_id, depth=1)
        node = (data.get("nodes", {}).get(node_id) or {}).get("document")
        if not node:
            raise RuntimeError(f"в Figma не найден фрейм {node_id}")
        return node

    def slides(self, links: list, multi: bool) -> list:
        """Список (key, node_id). Ссылка на секцию/группу раскрывается в её фреймы слева направо."""
        out = []
        for link in links:
            key, node_id = parse_figma(link)
            if multi and len(links) == 1:
                node = self._children_frames(key, node_id)
                kids = [c for c in node.get("children", []) if c.get("type") in SLIDE_TYPES]
                if node.get("type") in CONTAINER_TYPES or (node.get("type") == "FRAME" and len(kids) >= 2
                                                            and all(k.get("type") == "FRAME" for k in kids)):
                    kids.sort(key=lambda c: ((c.get("absoluteBoundingBox") or {}).get("y", 0) // 200,
                                             (c.get("absoluteBoundingBox") or {}).get("x", 0)))
                    out.extend((key, k["id"]) for k in kids)
                    continue
            out.append((key, node_id))
        return out

    def export(self, slides: list, fmt: str = "jpg") -> list:
        """Возвращает публичные ссылки на JPG (живут ~30 дней) в порядке слайдов."""
        urls = {}
        by_key = {}
        for key, nid in slides:
            by_key.setdefault(key, []).append(nid)
        for key, ids in by_key.items():
            data = self._get(f"/images/{key}", ids=",".join(ids), format=fmt, scale=self.scale)
            if data.get("err"):
                raise RuntimeError(f"Figma не смогла выгрузить картинки: {data['err']}")
            for nid, url in (data.get("images") or {}).items():
                urls[(key, nid)] = url
        missing = [nid for k, nid in slides if not urls.get((k, nid))]
        if missing:
            raise RuntimeError(f"Figma не отдала картинки для фреймов {missing}")
        return [urls[s] for s in slides]


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return dest


def drive_id(link: str):
    m = re.search(r"/d/([a-zA-Z0-9_-]{10,})", link) or re.search(r"[?&]id=([a-zA-Z0-9_-]{10,})", link)
    return m.group(1) if m else None


def fetch_drive(link: str, dest: Path) -> Path:
    """Файл с Google Drive: сначала по публичной ссылке, затем от имени сервисного аккаунта."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fid = drive_id(link)
    if not fid:
        raise RuntimeError(f"не понял ссылку на Google Drive: {link}")
    try:
        import gdown
        path = gdown.download(id=fid, output=str(dest), quiet=True)
        if path and Path(path).exists() and Path(path).stat().st_size > 1000 and not _is_html(Path(path)):
            return Path(path)
    except Exception:
        pass
    if env("GOOGLE_SA_JSON"):
        from .sheets import drive_download
        try:
            return drive_download(fid, dest)
        except Exception:
            pass
    raise RuntimeError("не удалось скачать файл с Google Drive — откройте доступ «Все, у кого есть ссылка» "
                       "или откройте файл/папку для сервисного аккаунта")


def _is_html(path: Path) -> bool:
    with open(path, "rb") as f:
        head = f.read(512).lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def kind(path: Path) -> str:
    """image | video по сигнатуре файла."""
    with open(path, "rb") as f:
        head = f.read(32)
    if head[4:8] == b"ftyp" or head[:4] in (b"\x1aE\xdf\xa3",) or head[:3] == b"FLV":
        return "video"
    return "image"


def to_jpeg(path: Path) -> Path:
    """Instagram принимает только JPEG."""
    from PIL import Image
    out = path.with_suffix(".jpg")
    with Image.open(path) as im:
        im.convert("RGB").save(out, "JPEG", quality=92)
    return out


def fetch_video(link: str, workdir: Path) -> Path:
    if "drive.google.com" in link or "docs.google.com" in link:
        return fetch_drive(link, workdir / "video.mp4")
    if "dropbox.com" in link:
        link = re.sub(r"dl=0", "dl=1", link)
    return download(link, workdir / "video.mp4")


def host_public(paths: list, post_id: str) -> list:
    """Кладёт картинки в ветку media репозитория и возвращает публичные ссылки (нужно Instagram)."""
    import shutil
    import subprocess
    import tempfile
    repo, token = env("GITHUB_REPOSITORY"), env("GITHUB_TOKEN")
    if not (repo and token):
        raise RuntimeError("для картинок с Google Drive в Instagram нужен запуск на GitHub (GITHUB_TOKEN)")
    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    tmp = Path(tempfile.mkdtemp(prefix="media-"))

    def git(*args, check=True):
        return subprocess.run(["git", *args], cwd=tmp, check=check, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.name", "speaks-autopost")
    git("config", "user.email", "speaks-autopost@users.noreply.github.com")
    git("remote", "add", "origin", url)
    if git("fetch", "-q", "--depth", "1", "origin", "media", check=False).returncode == 0:
        git("checkout", "-q", "-b", "media", "FETCH_HEAD")
    else:
        git("checkout", "-q", "--orphan", "media")
    folder = tmp / post_id
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for i, p in enumerate(paths, start=1):
        name = f"{i:02d}.jpg"
        shutil.copy(p, folder / name)
        names.append(name)
    git("add", ".")
    git("commit", "-q", "-m", f"media {post_id}", check=False)
    git("push", "-q", "origin", "media")
    shutil.rmtree(tmp, ignore_errors=True)
    return [f"https://raw.githubusercontent.com/{repo}/media/{post_id}/{n}" for n in names]
