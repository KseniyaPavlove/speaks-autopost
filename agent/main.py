"""Главный цикл агента: читает очередь, присылает превью, публикует то, чему пришло время."""
import datetime as dt
import shutil
import tempfile
from pathlib import Path

from . import captions, media, notify, queue
from .config import (MAX_ATTEMPTS, MAX_LATE_HOURS, PLATFORM_NAMES, PREVIEW_HOURS, REQUIRE_READY,
                     TZ, configured_platforms, env)
from .platforms import Job, get as get_platform
from .state import State

DONE = {"ok", "skipped"}


def names(codes):
    return ", ".join(PLATFORM_NAMES[c] for c in codes)


def _texts_report(texts: dict) -> str:
    parts = []
    for p, t in texts.items():
        if isinstance(t, dict):
            t = f"{t['title']}\n{t['description']}"
        parts.append(f"— {PLATFORM_NAMES[p]} —\n{t.strip() or '(без текста)'}")
    return "\n\n".join(parts)


def prepare_media(post, platforms, workdir: Path, dry_run=False, with_video=True):
    """Готовит картинки/видео для поста. В dry-run без ключа Figma — заглушки."""
    urls, paths, video = [], [], None
    need_images = post.format in ("post", "carousel") or (post.format == "story" and not post.video)
    if need_images:
        if dry_run and not env("FIGMA_TOKEN"):
            urls = [f"https://example.com/{i}.jpg" for i in range(len(post.figma))]
        else:
            fg = media.Figma()
            slides = fg.slides(post.figma, multi=post.format == "carousel")
            if post.format == "post":
                slides = slides[:1]
            urls = fg.export(slides)
            if set(platforms) & {"tg", "vk"}:
                paths = [media.download(u, workdir / f"slide{i + 1:02d}.jpg") for i, u in enumerate(urls)]
    if with_video and post.video and post.format in ("reels", "story") and not dry_run:
        video = media.fetch_video(post.video, workdir)
    return urls, paths, video


def run(source=None, dry_run=False, now=None, only=None, log=print):
    source = source or env("QUEUE_URL")
    if not source:
        raise SystemExit("Не задан QUEUE_URL — ссылка на Google-таблицу с очередью")
    now = now or dt.datetime.now(TZ)
    posts = queue.load(source)
    state = State()
    ready_platforms = configured_platforms()
    if dry_run:
        ready_platforms = {"ig", "tg", "vk", "yt", "tt"}

    seen = {}
    for p in posts:
        if p.id in seen:
            p.errors.append(f"id «{p.id}» повторяется (строки {seen[p.id]} и {p.line})")
        seen.setdefault(p.id, p.line)

    summary = []
    for post in posts:
        if only and post.id != only:
            continue
        if REQUIRE_READY and not post.ready:
            continue
        if post.errors:
            key = f"err:{post.id}:{post.fingerprint}"
            if not state.flag(key):
                notify.admin(f"❗ Строка {post.line} ({post.id}) не будет опубликована:\n• " + "\n• ".join(post.errors))
                state.set_flag(key)
            continue
        missing = [p for p in post.platforms if p not in ready_platforms]
        if missing and not state.flag(f"nocfg:{post.id}"):
            notify.admin(f"ℹ️ {post.id}: не подключены {names(missing)} — пропускаю их")
            state.set_flag(f"nocfg:{post.id}")
        if post.skipped and not state.flag(f"unsup:{post.id}"):
            notify.admin(f"ℹ️ {post.id}: формат «{post.format}» не публикуется в {names(post.skipped)} через API")
            state.set_flag(f"unsup:{post.id}")
        platforms = [p for p in post.platforms if p in ready_platforms]
        if not platforms:
            continue

        until = post.when - now
        # ---------- превью ----------
        if dt.timedelta(0) < until <= dt.timedelta(hours=PREVIEW_HOURS):
            key = f"preview:{post.id}:{post.fingerprint}"
            if not state.flag(key):
                texts = _captions(post, platforms, state, log, dry_run)
                workdir = Path(tempfile.mkdtemp(prefix="speaks-"))
                try:
                    urls, paths, _ = prepare_media(post, ["tg"], workdir, dry_run, with_video=False)
                except Exception as e:
                    paths = []
                    texts_note = f"\n\n⚠️ Медиа не подготовились: {e}"
                else:
                    texts_note = ""
                head = (f"👀 Превью: {post.id} — {post.format}, {post.when:%d.%m %H:%M}\n"
                        f"Куда: {names(platforms)}\n"
                        f"Поправить текст можно в колонках текст_tg / текст_vk / … таблицы.{texts_note}\n\n")
                notify.admin(head + _texts_report(texts), photo_path=paths[0] if paths else None)
                shutil.rmtree(workdir, ignore_errors=True)
                state.set_flag(key)
            continue
        if until > dt.timedelta(0):
            continue

        # ---------- публикация ----------
        pending = [p for p in platforms
                   if state.status(post.id, p) not in DONE and state.attempts(post.id, p) < MAX_ATTEMPTS]
        if not pending:
            continue
        if -until > dt.timedelta(hours=MAX_LATE_HOURS) and not any(state.status(post.id, p) for p in pending):
            for p in pending:
                state.record(post.id, p, "skipped", reason="опоздали")
            notify.admin(f"⏰ {post.id} ({post.when:%d.%m %H:%M}) не опубликован: прошло больше "
                         f"{MAX_LATE_HOURS} ч. Поменяйте дату в таблице, если он ещё нужен.")
            continue

        texts = _captions(post, platforms, state, log, dry_run)
        workdir = Path(tempfile.mkdtemp(prefix="speaks-"))
        report = []
        try:
            urls, paths, video = prepare_media(post, pending, workdir, dry_run)
        except Exception as e:
            for p in pending:
                state.record(post.id, p, "error", error=f"медиа: {e}")
            notify.admin(f"❌ {post.id}: не удалось подготовить медиа — {e}")
            shutil.rmtree(workdir, ignore_errors=True)
            continue
        for p in pending:
            job = Job(post_id=post.id, format=post.format, caption=texts.get(p),
                      image_urls=urls, image_paths=paths, video_path=video)
            if dry_run:
                report.append(f"🧪 {PLATFORM_NAMES[p]}: опубликовал бы ({len(urls)} карт., видео: {bool(post.video)})")
                continue
            try:
                res = get_platform(p).publish(job)
                state.record(post.id, p, "ok", url=res.get("url"), note=res.get("note"))
                line = f"✅ {PLATFORM_NAMES[p]}: {res.get('url') or 'готово'}"
                if res.get("note"):
                    line += f"\n   ⚠️ {res['note']}"
            except Exception as e:
                state.record(post.id, p, "error", error=str(e)[:500])
                left = MAX_ATTEMPTS - state.attempts(post.id, p)
                line = f"❌ {PLATFORM_NAMES[p]}: {e}" + (f" (повторю, осталось попыток: {left})" if left > 0 else " (больше не пытаюсь)")
            report.append(line)
        shutil.rmtree(workdir, ignore_errors=True)
        msg = f"📣 {post.id} — {post.format}, {post.when:%d.%m %H:%M}\n" + "\n".join(report)
        if dry_run:
            msg += "\n\n" + _texts_report(texts)
        notify.admin(msg) if not dry_run else log(msg)
        summary.append(msg)
        if not dry_run:
            state.save()  # сохраняем после каждого поста, чтобы не задублировать при сбое

    if not dry_run:
        state.save()
    return summary


def _captions(post, platforms, state, log, dry_run=False):
    cached = state.captions(post.id, post.fingerprint)
    if cached and all(p in cached for p in platforms):
        return {p: cached[p] for p in platforms}
    texts = captions.build(post, platforms, log=log)
    if not dry_run:
        state.save_captions(post.id, post.fingerprint, texts)
    return texts


def check(source=None):
    """Проверка ключей и таблицы — запускать после настройки."""
    lines = []
    source = source or env("QUEUE_URL")
    try:
        posts = queue.load(source)
        ready = [p for p in posts if p.ready]
        bad = [p for p in posts if p.errors]
        lines.append(f"✅ Таблица: строк {len(posts)}, готовых {len(ready)}, с ошибками {len(bad)}")
        for p in bad[:10]:
            lines.append(f"   • строка {p.line}: {'; '.join(p.errors)}")
    except Exception as e:
        lines.append(f"❌ Таблица: {e}")
    try:
        import requests
        me = requests.get("https://api.figma.com/v1/me", headers={"X-Figma-Token": env("FIGMA_TOKEN", "")}, timeout=30)
        lines.append(f"✅ Figma: {me.json()['email']}" if me.ok else f"❌ Figma: {me.status_code} {me.text[:100]}")
    except Exception as e:
        lines.append(f"❌ Figma: {e}")
    lines.append("✅ Claude: ключ задан" if env("ANTHROPIC_API_KEY") else
                 "ℹ️ Claude: ключа нет — тексты адаптируются по простым правилам")
    ok = configured_platforms()
    for code, name in PLATFORM_NAMES.items():
        if code not in ok:
            lines.append(f"➖ {name}: не подключён")
            continue
        try:
            lines.append(f"✅ {name}: {get_platform(code).check()}")
        except Exception as e:
            lines.append(f"❌ {name}: {e}")
    text = "🔧 Проверка агента S.peaks\n" + "\n".join(lines)
    notify.admin(text)
    return text
