"""Адаптация подписи под каждую площадку: через Claude API, а без ключа — по простым правилам."""
import json
import re

import requests

from .config import BRAND_FILE, env

LIMITS = {"ig": 2200, "tg": 4000, "vk": 15000, "tt": 2200, "yt_title": 100, "yt_desc": 5000}
DIRECT_RE = re.compile(
    r"(напиши(?:те)?\s+)?(?:нам\s+)?в\s+(?:директ|direct|дирeкт|личные сообщения|лс)\b", re.I)
HASHTAG_RE = re.compile(r"(?:^|\s)#[\w\d_]+", re.U)


def contacts() -> dict:
    return {
        "tg": env("CONTACT_TG", ""),      # например @speaks_manager
        "site": env("CONTACT_SITE", ""),  # например https://speaks.school
    }


def needed_keys(platforms) -> list:
    return [p for p in platforms if p != "ig"]


# ---------- без ИИ ----------

def _replace_direct(text, with_verb, bare):
    """«Напишите в директ» -> фраза площадки. with_verb может содержать {verb} — исходный глагол."""
    def repl(m):
        if m.group(1):
            out = with_verb.format(verb=m.group(1).strip())
        else:
            out = bare
        return out[:1].upper() + out[1:] if m.group(0)[:1].isupper() else out
    return DIRECT_RE.sub(repl, text)


def _strip_hashtags(text, keep=0):
    tags = [t.strip() for t in HASHTAG_RE.findall(text)]
    body = HASHTAG_RE.sub("", text).rstrip()
    body = re.sub(r"[ \t]+\n", "\n", body)
    return body + ("\n\n" + " ".join(tags[:keep]) if keep and tags else "")


def rule_based(text: str, platform: str, fmt: str):
    c = contacts()
    if platform == "tg":
        phrase = f"в личные сообщения {c['tg']}".strip() if c["tg"] else "в личные сообщения"
        return _strip_hashtags(_replace_direct(text, "{verb} " + phrase, phrase), keep=0)[: LIMITS["tg"]]
    if platform == "vk":
        return _strip_hashtags(_replace_direct(text, "{verb} в сообщения сообщества", "в сообщения сообщества"), keep=5)
    if platform == "tt":
        short = _strip_hashtags(_replace_direct(text, "ссылка для записи — в профиле", "по ссылке в профиле"), keep=5)
        return short[: LIMITS["tt"]]
    if platform == "yt":
        body = _strip_hashtags(_replace_direct(text, "записаться можно по ссылке в описании", "по ссылке в описании"), keep=0)
        first = re.split(r"(?<=[.!?…])\s|\n", body.strip(), maxsplit=1)[0].strip()
        title = (first[:85].rsplit(" ", 1)[0] if len(first) > 85 else first) or "S.peaks"
        desc = body + (f"\n\n{c['site']}" if c["site"] else "")
        return {"title": f"{title} #shorts", "description": desc[: LIMITS["yt_desc"]]}
    return text


# ---------- через Claude ----------

PROMPT = """Ты — SMM-редактор онлайн-школы S.peaks. Перепиши подпись к публикации под площадки.

Правила бренда и площадок:
<brand>
{brand}
</brand>

Контакты для призывов: Telegram менеджера — {tg}; сайт — {site}. Если контакт пустой — не выдумывай его.

Формат публикации: {fmt}
Исходный текст (написан для Instagram):
<source>
{text}
</source>

Нужны версии для: {keys}.
Сохрани смысл, факты, цены, даты и кодовые слова. Ничего не добавляй от себя.
Ответь ТОЛЬКО JSON-объектом без пояснений, где ключи:
"tg", "vk", "tt" — строки с готовым текстом; "yt" — объект {{"title": "...", "description": "..."}}.
Включи только запрошенные ключи."""


def ai_adapt(text, fmt, keys) -> dict:
    api_key = env("ANTHROPIC_API_KEY")
    brand = BRAND_FILE.read_text(encoding="utf-8") if BRAND_FILE.exists() else ""
    c = contacts()
    prompt = PROMPT.format(brand=brand, tg=c["tg"] or "—", site=c["site"] or "—",
                           fmt=fmt, text=text, keys=", ".join(keys))
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": env("CLAUDE_MODEL", "claude-sonnet-5-5"), "max_tokens": 4000,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=180,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Claude API {r.status_code}: {r.text[:300]}")
    out = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    start, end = out.find("{"), out.rfind("}")
    data = json.loads(out[start:end + 1])
    return {k: data[k] for k in keys if k in data}


def _clip(texts: dict) -> dict:
    for k in ("tg", "vk", "tt"):
        if isinstance(texts.get(k), str):
            texts[k] = texts[k][: LIMITS[k]]
    if isinstance(texts.get("yt"), dict):
        yt = texts["yt"]
        yt["title"] = re.sub(r"[<>]", "", yt.get("title", ""))[: LIMITS["yt_title"]]
        yt["description"] = re.sub(r"[<>]", "", yt.get("description", ""))[: LIMITS["yt_desc"]]
    return texts


def build(post, platforms, log=print) -> dict:
    """Возвращает {platform: текст} (для yt — {"title", "description"})."""
    o = post.overrides
    texts = {}
    if "ig" in platforms:
        texts["ig"] = o.get("text_ig") or post.text
    todo = []
    for p in needed_keys(platforms):
        if p == "yt":
            if o.get("title_yt") and o.get("text_yt"):
                texts["yt"] = {"title": o["title_yt"], "description": o["text_yt"]}
                continue
        elif o.get(f"text_{p}"):
            texts[p] = o[f"text_{p}"]
            continue
        todo.append(p)

    if todo and post.text:
        generated = {}
        if env("ANTHROPIC_API_KEY"):
            try:
                generated = ai_adapt(post.text, post.format, todo)
            except Exception as e:  # не роняем публикацию из-за ИИ
                log(f"⚠️ Claude не ответил ({e}); беру тексты по правилам")
        for p in todo:
            texts[p] = generated.get(p) or rule_based(post.text, p, post.format)
    elif todo:
        for p in todo:
            texts[p] = {"title": "S.peaks #shorts", "description": ""} if p == "yt" else ""

    # ручные заголовок/описание YouTube перекрывают сгенерированные
    if "yt" in texts and isinstance(texts["yt"], dict):
        if o.get("title_yt"):
            texts["yt"]["title"] = o["title_yt"]
        if o.get("text_yt"):
            texts["yt"]["description"] = o["text_yt"]
    return _clip(texts)
