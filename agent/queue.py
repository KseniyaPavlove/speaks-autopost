"""Чтение контент-плана: Google-таблица (через сервисный аккаунт) или CSV для тестов.

Агент понимает «живой» контент-план S.peaks: колонки «Дата», «Время», «Формат», «Площадки»,
«Тема / заголовок», «Ссылка на макет», «Подпись к публикации», «Статус», «Ссылка на публикацию».
Публикуются только строки со статусом «Готово к публикации».
"""
import csv
import datetime as dt
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import TZ, env

# Заголовки таблицы -> внутренние ключи. Регистр, пробелы и «ё» не важны.
HEADERS = {
    "id": "id",
    "дата": "date",
    "время": "time",
    "время публикации": "time",
    "формат": "format",
    "площадки": "platforms",
    "тема / заголовок": "topic",
    "тема": "topic",
    "заголовок": "topic",
    "ссылка на макет": "media",
    "макет": "media",
    "figma": "media",
    "фигма": "media",
    "видео": "media",
    "подпись к публикации": "text",
    "подпись": "text",
    "текст": "text",
    "текст_ig": "text_ig",
    "текст_tg": "text_tg",
    "текст_vk": "text_vk",
    "текст_tt": "text_tt",
    "заголовок_yt": "title_yt",
    "текст_yt": "text_yt",
    "статус": "status",
    "ссылка на публикацию": "links",
}

FORMATS = {
    "пост": "post", "post": "post", "фото": "post", "картинка": "post",
    "карусель": "carousel", "carousel": "carousel",
    "reels": "reels", "reel": "reels", "рилс": "reels", "видео": "reels", "shorts": "reels", "клип": "reels",
    "сторис": "story", "stories": "story", "story": "story",
}

# Площадки из плана, которые агент пока не публикует (не ошибка — просто пропускаем)
KNOWN_UNSUPPORTED = {"facebook": "Facebook", "facebook reels": "Facebook", "fb": "Facebook",
                     "max": "MAX", "макс": "MAX", "сайт": "сайт"}

PLATFORM_ALIASES = {
    "instagram": "ig", "instagram stories": "ig", "ig": "ig", "инстаграм": "ig", "инста": "ig",
    "telegram": "tg", "tg": "tg", "телеграм": "tg", "тг": "tg",
    "vk": "vk", "вк": "vk", "вконтакте": "vk", "vk клипы": "vk",
    "youtube": "yt", "youtube shorts": "yt", "shorts": "yt", "yt": "yt", "ютуб": "yt",
    "tiktok": "tt", "tt": "tt", "тикток": "tt",
}

SUPPORTED = {
    "post": ["ig", "tg", "vk"],
    "carousel": ["ig", "tg", "vk"],
    "reels": ["ig", "tg", "vk", "yt", "tt"],
    "story": ["ig"],
}

READY_WORDS = {"готово к публикации", "готово", "ready", "автопостинг"}
DONE_STATUS = "Опубликовано"
FAILED_STATUS = "Ошибка публикации"

DATE_FORMATS = ["%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y"]
URL_RE = re.compile(r"https?://[^\s,;]+")


@dataclass
class Cell:
    text: str = ""
    links: list = field(default_factory=list)

    @property
    def all_links(self):
        out = list(self.links)
        for u in URL_RE.findall(self.text):
            if u not in out:
                out.append(u)
        return out


@dataclass
class Post:
    id: str
    line: int
    when: dt.datetime | None
    format: str | None
    platforms: list
    figma: list
    drive: list
    topic: str
    text: str
    overrides: dict
    status: str
    sheet: str = ""
    published_links: str = ""
    columns: dict = field(default_factory=dict)  # ключ -> индекс колонки (для записи обратно)
    skipped: list = field(default_factory=list)
    other: list = field(default_factory=list)  # Facebook, MAX, сайт — пока вручную
    errors: list = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return norm(self.status) in READY_WORDS

    @property
    def video(self):
        return self.drive[0] if self.format == "reels" and self.drive else ""

    @property
    def title(self):
        return self.topic or (self.text.splitlines() or [""])[0][:60] or self.id

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            [self.when.isoformat() if self.when else None, self.format, self.platforms,
             self.figma, self.drive, self.text, self.overrides],
            ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(payload.encode()).hexdigest()[:12]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower().replace("ё", "е"))


def _parse_when(date_s, time_s, errors):
    date_s = (date_s or "").strip()
    time_s = (time_s or "").strip() or env("DEFAULT_TIME", "12:00")
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
            day = dt.date(dt.datetime.now(TZ).year, int(m.group(2)), int(m.group(1)))
    if day is None:
        errors.append(f"не понял дату «{date_s}» (нужно ДД.ММ.ГГГГ)")
        return None
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", time_s)
    if not m:
        errors.append(f"не понял время «{time_s}» (нужно ЧЧ:ММ)")
        return None
    return dt.datetime.combine(day, dt.time(int(m.group(1)), int(m.group(2))), tzinfo=TZ)


def _platforms(raw: str, errors: list, other: list):
    raw = re.sub(r"(?i)дубли\s*:", ",", raw or "")
    parts = [norm(p) for p in re.split(r"[,;\n/+]+", raw) if p.strip()]
    if not parts or parts in (["все"], ["all"]):
        return ["ig", "tg", "vk", "yt", "tt"], False
    out = []
    for p in parts:
        code = PLATFORM_ALIASES.get(p) or PLATFORM_ALIASES.get(p.split()[0])
        if code is None:
            name = KNOWN_UNSUPPORTED.get(p) or KNOWN_UNSUPPORTED.get(p.split()[0])
            if name:
                if name not in other:
                    other.append(name)
            else:
                errors.append(f"неизвестная площадка «{p}»")
        elif code not in out:
            out.append(code)
    return out, True


def _format(raw: str):
    """«Серия Stories; 3 кадра» -> story, «Reels; блогер» -> reels, «Лонгрид №3» -> None (не соцсети)."""
    head = norm(re.split(r"[;(]", raw or "")[0])
    if head in FORMATS:
        return FORMATS[head]
    for key, fmt in (("stor", "story"), ("сторис", "story"), ("reel", "reels"), ("рилс", "reels"),
                     ("клип", "reels"), ("карусел", "carousel"), ("пост", "post")):
        if key in head:
            return fmt
    return None


def is_figma(u):
    return "figma.com" in u


def is_drive(u):
    return "drive.google.com" in u or "docs.google.com/uc" in u


def parse_table(rows: list, sheet: str = "") -> list:
    """rows — список строк, каждая строка — список Cell. Первая строка — заголовки."""
    if not rows:
        return []
    columns = {}
    for j, c in enumerate(rows[0]):
        key = HEADERS.get(norm(c.text))
        if key and key not in columns:
            columns[key] = j
    if "date" not in columns or "status" not in columns:
        return []  # не контент-план

    def get(row, key) -> Cell:
        j = columns.get(key)
        return row[j] if j is not None and j < len(row) else Cell()

    posts = []
    for i, row in enumerate(rows[1:], start=2):
        status = get(row, "status").text
        if not any(c.text.strip() for c in row):
            continue
        errors = []
        when = _parse_when(get(row, "date").text, get(row, "time").text, errors)
        fmt_raw = get(row, "format").text.strip()
        fmt = _format(fmt_raw)
        if not fmt:
            errors.append(f"формат «{fmt_raw}» агент не публикует (пост / карусель / reels / stories)")
        other = []
        wanted, explicit = _platforms(get(row, "platforms").text, errors, other)
        allowed = SUPPORTED.get(fmt, [])
        platforms = [p for p in wanted if p in allowed]
        skipped = [p for p in wanted if p not in allowed] if explicit else []

        links = get(row, "media").all_links
        figma = [u for u in links if is_figma(u)]
        drive = [u for u in links if is_drive(u)]
        if fmt in ("post", "carousel", "story") and not (figma or drive):
            errors.append("в «Ссылке на макет» нет ссылки на Figma или Google Drive")
        if fmt == "reels" and not drive:
            errors.append("для Reels нужна ссылка на видео в Google Drive в «Ссылке на макет»")

        overrides = {k: get(row, k).text.strip()
                     for k in ("text_ig", "text_tg", "text_vk", "text_tt", "title_yt", "text_yt")
                     if get(row, k).text.strip()}
        topic = get(row, "topic").text.strip()
        pid = get(row, "id").text.strip()
        if not pid and when:
            key = topic or get(row, "text").text or " ".join(links)
            pid = f"{when:%Y%m%d}-{fmt or 'x'}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"
        posts.append(Post(
            id=pid or f"{sheet}-{i}", line=i, when=when, format=fmt, platforms=platforms,
            figma=figma, drive=drive, topic=topic, text=get(row, "text").text.strip(),
            overrides=overrides, status=status, sheet=sheet, columns=columns,
            published_links=get(row, "links").text,
            skipped=skipped, other=other, errors=errors))
    return posts


# ---------- источники ----------

def load_csv(path: str) -> list:
    text = Path(path).read_text(encoding="utf-8-sig")
    rows = [[Cell(text=v) for v in r] for r in csv.reader(io.StringIO(text))]
    return parse_table(rows, sheet=Path(path).stem)


def load(source: str | None = None) -> list:
    source = source or env("QUEUE_URL")
    if not source:
        raise SystemExit("Не задан QUEUE_URL — ссылка на контент-план")
    if not source.startswith("http"):
        return load_csv(source)
    from .sheets import Sheet
    sheet = Sheet(source)
    posts = []
    for title, rows in sheet.tables():
        posts.extend(parse_table(rows, sheet=title))
    return posts
