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


def fetch_video(link: str, workdir: Path) -> Path:
    dest = workdir / "video.mp4"
    if "drive.google.com" in link or "docs.google.com" in link:
        import gdown  # тянем только когда нужно
        path = gdown.download(url=link, output=str(dest), quiet=True, fuzzy=True)
        if not path or not Path(path).exists() or Path(path).stat().st_size < 10_000:
            raise RuntimeError("не удалось скачать видео с Google Drive — откройте доступ по ссылке")
        return Path(path)
    if "dropbox.com" in link:
        link = re.sub(r"dl=0", "dl=1", link)
    return download(link, dest)
