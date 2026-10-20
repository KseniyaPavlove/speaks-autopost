"""Чтение очереди публикаций из Google-таблицы (или локального CSV)."""
import csv
import datetime as dt
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from .config import TZ

# Заголовки таблицы -> внутренние ключи. Регистр и пробелы не важны.
HEADERS = {
    "id": "id",
    "дата": "date",
    "время": "time",
    "формат": "format",
    "площадки": "platforms",
    "figma": "figma",
    "фигма": "figma",
    "видео": "video",
    "текст": "text",
    "текст_ig": "text_ig",
    "текст_tg": "text_tg",
    "текст_vk": "text_vk",
    "текст_tt": "text_tt",
    "заголовок_yt": "title_yt",
    "текст_yt": "text_yt",
    "статус": "status",
    "комментарий": "note",
}

FORMATS = {
    "пост": "post", "post": "post", "фото": "post", "картинка": "post",
    "карусель": "carousel", "carousel": "carousel",
    "reels": "reels", "рилс": "reels", "видео": "reels", "shorts": "reels", "клип": "reels",
    "сторис": "story", "stories": "story", "story": "story",
}

PLATFORM_ALIASES = {
    "ig": "ig", "instagram": "ig", "инста": "ig", "инстаграм": "ig", "inst": "ig",
    "tg": "tg", "telegram": "tg", "телеграм": "tg", "тг": "tg",
    "vk": "vk", "вк": "vk", "вконтакте": "vk",
    "yt": "yt", "youtube": "yt", "ютуб": "yt", "shorts": "yt",
    "tt": "tt", "tiktok": "tt", "тикток": "tt",
}

# Какие площадки умеют какой формат (через официальные API)
SUPPORTED = {
    "post": ["ig", "tg", "vk"],
    "carousel": ["ig", "tg", "vk"],
    "reels": ["ig", "tg", "vk", "yt", "tt"],
    "story": ["ig"],
}

READY_WORDS = {"готово", "ready", "да", "ok", "ок", "✅", "+", "в работу", "публиковать"}

DATE_FORMATS = ["%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y"]


@dataclass
class Post:
    id: str
    line: int
    when: dt.datetime | None
    format: str | None
    platforms: list
    figma: list
    video: str
    text: str
    overrides: dict
    status: str
    skipped: list = field(default_factory=list)  # запрошены, но формат не поддерживается
    errors: list = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.status.strip().lower() in READY_WORDS

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            [self.when.isoformat() if self.when else None, self.format, self.platforms,
             self.figma, self.video, self.text, self.overrides],
            ensure_ascii=False, sort_keys=True,
        )
        return hashlib.sha1(payload.encode()).hexdigest()[:12]


def sheet_csv_url(url: str) -> str:
    """Ссылку на Google-таблицу превращаем в ссылку на CSV-выгрузку нужного листа."""
    if "docs.google.com/spreadsheets" not in url or "format=csv" in url or "output=csv" in url:
        return url
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        return url
    gid = None
    parsed = urlparse(url)
    for part in (parsed.fragment, parsed.query):
        q = parse_qs(part)
        if "gid" in q:
            gid = q["gid"][0]
    out = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
    return out + (f"&gid={gid}" if gid else "")


def _read_source(source: str) -> str:
    if source.startswith("http"):
        r = requests.get(sheet_csv_url(source), timeout=60)
        r.raise_for_status()
        r.encoding = "utf-8"
        if r.text.lstrip().startswith("<"):
            raise RuntimeError(
                "Таблица не отдаёт CSV — откройте доступ «Все, у кого есть ссылка: читатель»"
            )
        return r.text
    return Path(source).read_text(encoding="utf-8-sig")


def _norm_header(h: str) -> str:
    key = re.sub(r"\s+", "_", (h or "").strip().lower())
    return HEADERS.get(key, key)


def _split(value: str) -> list:
    return [x for x in re.split(r"[\s,;]+", value or "") if x]


def _parse_when(date_s: str, time_s: str, errors: list):
    date_s, time_s = (date_s or "").strip(), (time_s or "").strip() or "10:00"
    day = None
    for fmt in DATE_FORMATS:
        try:
            day = dt.datetime.strptime(date_s, fmt).date()
            break
        except ValueError:
            pass
    if day is None:
        m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", date_s)
        if m:
            today = dt.datetime.now(TZ).date()
            day = dt.date(today.year, int(m.group(2)), int(m.group(1)))
    if day is None:
        errors.append(f"не понял дату «{date_s}» (нужно ДД.ММ.ГГГГ)")
        return None
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", time_s)
    if not m:
        errors.append(f"не понял время «{time_s}» (нужно ЧЧ:ММ)")
        return None
    return dt.datetime.combine(day, dt.time(int(m.group(1)), int(m.group(2))), tzinfo=TZ)


def parse_rows(text: str) -> list:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    headers = [_norm_header(h) for h in rows[0]]
    posts = []
    for i, raw in enumerate(rows[1:], start=2):
        d = {headers[j]: (raw[j] if j < len(raw) else "") for j in range(len(headers))}
        if not any(v.strip() for v in d.values()):
            continue
        errors = []
        when = _parse_when(d.get("date", ""), d.get("time", ""), errors)

        fmt_raw = d.get("format", "").strip().lower()
        fmt = FORMATS.get(fmt_raw)
        if not fmt:
            errors.append(f"неизвестный формат «{fmt_raw}» (пост / карусель / reels / сторис)")

        plat_raw = _split(d.get("platforms", "").lower())
        if not plat_raw or plat_raw == ["все"] or plat_raw == ["all"]:
            wanted = ["ig", "tg", "vk", "yt", "tt"]
        else:
            wanted = []
            for p in plat_raw:
                code = PLATFORM_ALIASES.get(p)
                if code is None:
                    errors.append(f"неизвестная площадка «{p}»")
                elif code not in wanted:
                    wanted.append(code)
        allowed = SUPPORTED.get(fmt, [])
        platforms = [p for p in wanted if p in allowed]
        explicit = plat_raw and plat_raw not in (["все"], ["all"])
        skipped = [p for p in wanted if p not in allowed] if explicit else []

        figma = [x for x in _split(d.get("figma", "")) if "figma.com" in x]
        video = d.get("video", "").strip()
        if fmt in ("post", "carousel") and not figma:
            errors.append("нет ссылки на фрейм Figma")
        if fmt == "reels" and not video:
            errors.append("для reels нужна ссылка на видео")
        if fmt == "story" and not (figma or video):
            errors.append("для сторис нужна ссылка на Figma или видео")

        overrides = {
            k: d.get(k, "").strip()
            for k in ("text_ig", "text_tg", "text_vk", "text_tt", "title_yt", "text_yt")
            if d.get(k, "").strip()
        }
        pid = (d.get("id") or "").strip()
        if not pid and when:
            pid = f"{when:%Y%m%d-%H%M}-{fmt or 'x'}"
        posts.append(Post(
            id=pid or f"line{i}", line=i, when=when, format=fmt, platforms=platforms,
            figma=figma, video=video, text=d.get("text", "").strip(), overrides=overrides,
            status=d.get("status", ""), skipped=skipped, errors=errors,
        ))
    return posts


def load(source: str) -> list:
    return parse_rows(_read_source(source))
