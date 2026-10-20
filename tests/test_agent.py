import datetime as dt
import json
from pathlib import Path

import pytest

from agent import captions, main, media, queue
from agent.config import TZ
from agent.platforms import tiktok
from agent.sheets import col_letter
from agent.state import State

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "queue_template.csv"


def by_topic(posts):
    return {p.topic: p for p in posts}


def test_parse_content_plan():
    posts = by_topic(queue.load(str(TEMPLATE)))
    story = posts["Слово дня — Mistake"]
    assert story.format == "story" and story.platforms == ["ig"] and not story.errors and story.ready
    reels = posts["Понимаете, но не можете ответить"]
    assert reels.format == "reels" and reels.platforms == ["ig", "vk", "yt", "tg"]
    assert reels.other == ["Facebook", "MAX"] and reels.video.startswith("https://drive.google.com")
    assert reels.when == dt.datetime(2026, 10, 21, 18, 30, tzinfo=TZ)
    carousel = posts["5 страхов перед онлайн-занятиями"]
    assert not carousel.ready and carousel.when.hour == 12  # без времени — DEFAULT_TIME
    longread = posts["Как выбрать язык под работу"]
    assert longread.format is None and longread.errors
    assert len({p.id for p in posts.values()}) == len(posts)


def test_unknown_values():
    rows = [[queue.Cell(t) for t in r] for r in [
        ["Дата", "Формат", "Площадки", "Ссылка на макет", "Статус"],
        ["31.02.2026", "мем", "Одноклассники", "", "Готово к публикации"],
        ["01.11.2026", "Пост", "Instagram, TikTok", "https://www.figma.com/design/K/F?node-id=1-2", "Готово к публикации"],
    ]]
    bad, post = queue.parse_table(rows)
    assert any("дату" in e for e in bad.errors) and any("формат" in e for e in bad.errors)
    assert any("площадка" in e for e in bad.errors)
    assert post.platforms == ["ig"] and post.skipped == ["tt"] and not post.errors


def test_hyperlinks_in_cells():
    cell = queue.Cell(text="макет", links=["https://www.figma.com/design/K/F?node-id=5-6"])
    assert cell.all_links == ["https://www.figma.com/design/K/F?node-id=5-6"]
    assert col_letter(0) == "A" and col_letter(25) == "Z" and col_letter(27) == "AB"


def test_figma_and_drive_links():
    assert media.parse_figma("https://www.figma.com/design/AbC123/Speaks?node-id=12-34&t=x") == ("AbC123", "12:34")
    assert media.parse_figma("https://www.figma.com/design/AbC/branch/BrX/Speaks?node-id=1%3A2") == ("BrX", "1:2")
    assert media.drive_id("https://drive.google.com/file/d/1hQ1fcwQiRmLnS-aX45IJ5YiFIffwiKrq/view?usp=share_link") \
        == "1hQ1fcwQiRmLnS-aX45IJ5YiFIffwiKrq"
    with pytest.raises(ValueError):
        media.parse_figma("https://www.figma.com/design/AbC/Speaks")


def test_rule_based_captions(monkeypatch):
    monkeypatch.setenv("CONTACT_TG", "@speaks_manager")
    text = "Боитесь говорить?\n\nНапишите в директ — методист поможет.\n\n#английский #speaks"
    tg = captions.rule_based(text, "tg", "post")
    assert "директ" not in tg and "@speaks_manager" in tg and "#" not in tg and tg.count("Напишите") == 1
    vk = captions.rule_based(text, "vk", "post")
    assert "в сообщения сообщества" in vk and "#английский" in vk
    yt = captions.rule_based(text, "yt", "reels")
    assert yt["title"] == "Боитесь говорить? #shorts"


def test_tiktok_chunks():
    mb = 1024 * 1024
    assert tiktok.chunks(3 * mb) == (3 * mb, 1)
    chunk, count = tiktok.chunks(95 * mb)
    assert chunk == 10 * mb and count == 9 and 95 * mb - chunk * (count - 1) <= 128 * mb


@pytest.fixture
def env_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "State", lambda: State(tmp_path / "published.json"))
    for k in ("ANTHROPIC_API_KEY", "TG_BOT_TOKEN", "ADMIN_CHAT_ID", "FIGMA_TOKEN", "GOOGLE_SA_JSON"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_full_cycle(env_clean, monkeypatch):
    """Превью → публикация → повтор после ошибки → без дублей → запись в таблицу."""
    published, alerts, written = [], [], []
    monkeypatch.setattr(main, "configured_platforms", lambda: {"ig", "tg", "vk", "yt", "tt"})
    monkeypatch.setattr(main.notify, "admin", lambda text, photo_path=None: alerts.append(text))
    monkeypatch.setattr(main, "prepare_media", lambda post, pl, wd, dry=False, with_video=True:
                        (["https://img/1.jpg"], [Path("/tmp/1.jpg")], Path("/tmp/v.mp4") if post.video else None))
    from agent import sheets
    monkeypatch.setattr(sheets, "write_back", lambda post, status, links, old: written.append((post.topic, status, links)) or True)
    flaky = {"vk": 1}

    class Fake:
        def __init__(self, code):
            self.code = code

        def publish(self, job):
            if flaky.get(self.code):
                flaky[self.code] -= 1
                raise RuntimeError("временная ошибка")
            published.append((job.post_id, self.code, job.caption))
            return {"url": f"https://{self.code}.example/{job.post_id}"}

    monkeypatch.setattr(main, "get_platform", Fake)
    src = str(TEMPLATE)
    reels_id = by_topic(queue.load(src))["Понимаете, но не можете ответить"].id

    main.run(source=src, now=dt.datetime(2026, 10, 21, 15, 30, tzinfo=TZ))
    assert all(pid != reels_id for pid, _, _ in published)
    assert any("Превью: «Понимаете" in a for a in alerts)
    assert any("Facebook, MAX" in a for a in alerts)

    main.run(source=src, now=dt.datetime(2026, 10, 21, 18, 35, tzinfo=TZ))
    assert {c for pid, c, _ in published if pid == reels_id} == {"ig", "tg", "yt"}
    tg_caption = next(cap for pid, c, cap in published if c == "tg")
    assert "директ" not in tg_caption
    assert not [w for w in written if w[0].startswith("Понимаете")]  # VK ещё не вышел

    published.clear()
    main.run(source=src, now=dt.datetime(2026, 10, 21, 18, 50, tzinfo=TZ))
    assert [(p, c) for p, c, _ in published] == [(reels_id, "vk")]
    status = [w for w in written if w[0].startswith("Понимаете")]
    assert status and status[0][1] == "Опубликовано" and len(status[0][2]) == 4

    main.run(source=src, now=dt.datetime(2026, 10, 23, 12, 5, tzinfo=TZ))
    assert all("5 страхов" not in str(w) for w in written)  # «Не начато» не трогаем


def test_too_late_is_skipped(env_clean, monkeypatch):
    alerts = []
    monkeypatch.setattr(main, "configured_platforms", lambda: {"ig"})
    monkeypatch.setattr(main.notify, "admin", lambda text, photo_path=None: alerts.append(text))
    monkeypatch.setattr(main, "get_platform", lambda c: pytest.fail("не должен публиковать"))
    story_id = by_topic(queue.load(str(TEMPLATE)))["Слово дня — Mistake"].id
    main.run(source=str(TEMPLATE), now=dt.datetime(2026, 10, 22, 10, 0, tzinfo=TZ), only=story_id)
    assert any("не опубликован" in a for a in alerts)


def test_sheets_api(monkeypatch):
    """Чтение вкладок с гиперссылками и запись статуса — на подменённом Google API."""
    from agent import sheets
    calls = []

    class R:
        def __init__(self, data):
            self._d, self.status_code, self.text = data, 200, ""

        def json(self):
            return self._d

        def raise_for_status(self):
            pass

    def v(text, link=None):
        d = {"formattedValue": text}
        if link:
            d["hyperlink"] = link
        return d

    class S:
        def get(self, url, params=None, **kw):
            if params.get("fields", "").startswith("sheets.properties"):
                return R({"sheets": [{"properties": {"title": "Контент план ОКТЯБРЬ"}},
                                     {"properties": {"title": "Преподаватели"}}]})
            if "ОКТЯБРЬ" in params["ranges"]:
                rows = [[v("Дата"), v("Время"), v("Формат"), v("Площадки"), v("Тема / заголовок"),
                         v("Ссылка на макет"), v("Подпись к публикации"), v("Статус"), v("Ссылка на публикацию")],
                        [v("22.10.2026"), v("12:00"), v("Карусель"), v("Instagram, Telegram"), v("Тест"),
                         v("макет", "https://www.figma.com/design/K/F?node-id=1-2"), v("Текст"),
                         v("Готово к публикации"), v("")]]
            else:
                rows = [[v("Имя"), v("Уровень")]]
            return R({"sheets": [{"data": [{"rowData": [{"values": r} for r in rows]}]}]})

        def post(self, url, json=None, **kw):
            calls.append(json)
            return R({})

    monkeypatch.setattr(sheets, "_session", S())
    monkeypatch.setenv("GOOGLE_SA_JSON", "{}")
    url = "https://docs.google.com/spreadsheets/d/1BL1k2ppLpIe9KN5L3g0254xKkE6G9sRP5phS5RQgkAw/edit"
    monkeypatch.setenv("QUEUE_URL", url)
    posts = queue.load(url)
    assert len(posts) == 1 and posts[0].figma == ["https://www.figma.com/design/K/F?node-id=1-2"]
    assert sheets.write_back(posts[0], "Опубликовано", ["https://t.me/speaks_school/1"], "")
    data = calls[0]["data"]
    assert {d["range"] for d in data} == {"'Контент план ОКТЯБРЬ'!H2", "'Контент план ОКТЯБРЬ'!I2"}
