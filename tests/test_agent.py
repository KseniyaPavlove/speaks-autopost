import datetime as dt
import json
from pathlib import Path

import pytest

from agent import captions, main, media, queue
from agent.config import TZ
from agent.platforms import tiktok
from agent.state import State

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "queue_template.csv"


def test_parse_template():
    posts = {p.id: p for p in queue.load(str(TEMPLATE))}
    assert set(posts) == {"word-mistake", "reels-understand", "carousel-online", "post-bloggers"}
    story = posts["word-mistake"]
    assert story.format == "story" and story.platforms == ["ig"] and not story.errors
    reels = posts["reels-understand"]
    assert reels.platforms == ["ig", "tg", "vk", "yt", "tt"]
    assert reels.when == dt.datetime(2026, 10, 21, 18, 30, tzinfo=TZ)
    assert not posts["carousel-online"].ready
    assert posts["post-bloggers"].overrides["text_tg"].startswith("Ищем блогеров")


def test_row_errors_and_unsupported():
    csv_text = "дата,время,формат,площадки,figma,текст,статус\n" \
               "31.02.2026,25:00,мем,ig,,x,готово\n" \
               "01.11.2026,10:00,пост,\"ig, tt, yt\",https://www.figma.com/design/K/F?node-id=1-2,x,готово\n"
    bad, post = queue.parse_rows(csv_text)
    assert any("дату" in e for e in bad.errors) and any("формат" in e for e in bad.errors)
    assert post.platforms == ["ig"] and post.skipped == ["tt", "yt"]


def test_figma_links():
    assert media.parse_figma("https://www.figma.com/design/AbC123/Speaks?node-id=12-34&t=x") == ("AbC123", "12:34")
    assert media.parse_figma("https://www.figma.com/design/AbC/branch/BrX/Speaks?node-id=1%3A2") == ("BrX", "1:2")
    with pytest.raises(ValueError):
        media.parse_figma("https://www.figma.com/design/AbC/Speaks")


def test_sheet_url():
    url = "https://docs.google.com/spreadsheets/d/1AbC_d-E/edit#gid=777"
    assert queue.sheet_csv_url(url) == "https://docs.google.com/spreadsheets/d/1AbC_d-E/export?format=csv&gid=777"


def test_rule_based_captions(monkeypatch):
    monkeypatch.setenv("CONTACT_TG", "@speaks_manager")
    text = "Боитесь говорить?\n\nНапишите в директ — методист поможет.\n\n#английский #speaks"
    tg = captions.rule_based(text, "tg", "post")
    assert "директ" not in tg and "@speaks_manager" in tg and "#" not in tg
    vk = captions.rule_based(text, "vk", "post")
    assert "в сообщения сообщества" in vk and "#английский" in vk
    yt = captions.rule_based(text, "yt", "reels")
    assert yt["title"] == "Боитесь говорить? #shorts"


def test_overrides_win(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    post = queue.parse_rows("дата,время,формат,площадки,видео,текст,текст_tg,заголовок_yt,статус\n"
                            "01.11.2026,10:00,reels,все,https://x/v.mp4,Базовый текст,Свой TG,Свой заголовок,готово\n")[0]
    t = captions.build(post, post.platforms)
    assert t["ig"] == "Базовый текст" and t["tg"] == "Свой TG" and t["yt"]["title"] == "Свой заголовок"


def test_tiktok_chunks():
    mb = 1024 * 1024
    assert tiktok.chunks(3 * mb) == (3 * mb, 1)
    chunk, count = tiktok.chunks(95 * mb)
    assert chunk == 10 * mb and count == 9 and 95 * mb - chunk * (count - 1) <= 128 * mb


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "State", lambda: State(tmp_path / "published.json"))
    for k in ("ANTHROPIC_API_KEY", "TG_BOT_TOKEN", "ADMIN_CHAT_ID", "FIGMA_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_full_cycle_with_fake_platforms(isolated_state, monkeypatch):
    """Прогон: превью → публикация → повтор после ошибки → без дублей."""
    published, alerts = [], []
    monkeypatch.setattr(main, "configured_platforms", lambda: {"ig", "tg", "vk", "yt", "tt"})
    monkeypatch.setattr(main.notify, "admin", lambda text, photo_path=None: alerts.append(text))
    monkeypatch.setattr(main, "prepare_media", lambda post, pl, wd, dry=False, with_video=True:
                        (["https://img/1.jpg"], [Path("/tmp/1.jpg")], Path("/tmp/v.mp4") if post.video else None))
    flaky = {"vk": 1}

    class Fake:
        def __init__(self, code):
            self.code = code

        def publish(self, job):
            if flaky.get(self.code):
                flaky[self.code] -= 1
                raise RuntimeError("временная ошибка")
            published.append((job.post_id, self.code, job.caption))
            return {"url": f"https://{self.code}/{job.post_id}"}

    monkeypatch.setattr(main, "get_platform", Fake)
    src = str(TEMPLATE)

    # за 3 часа до reels — только превью
    main.run(source=src, now=dt.datetime(2026, 10, 21, 15, 30, tzinfo=TZ))
    assert all(pid != "reels-understand" for pid, _, _ in published)
    assert any("Превью: reels-understand" in a for a in alerts)
    assert ("word-mistake", "ig") in [(p, c) for p, c, _ in published]  # сторис 10:00 опоздала на 5,5 ч — ещё в окне

    # время пришло — публикуем; VK падает один раз
    main.run(source=src, now=dt.datetime(2026, 10, 21, 18, 35, tzinfo=TZ))
    codes = {c for pid, c, _ in published if pid == "reels-understand"}
    assert codes == {"ig", "tg", "yt", "tt"}
    tg_caption = next(cap for pid, c, cap in published if c == "tg")
    assert "директ" not in tg_caption

    # следующий запуск: дожимаем VK, остальное не дублируем
    published.clear()
    main.run(source=src, now=dt.datetime(2026, 10, 21, 18, 50, tzinfo=TZ))
    assert [(p, c) for p, c, _ in published] == [("reels-understand", "vk")]

    # черновик (carousel-online) не публикуется никогда
    main.run(source=src, now=dt.datetime(2026, 10, 23, 12, 5, tzinfo=TZ))
    assert all(pid != "carousel-online" for pid, _, _ in published)

    state = json.loads((isolated_state / "published.json").read_text())
    assert state["posts"]["reels-understand"]["vk"]["status"] == "ok"


def test_too_late_is_skipped(isolated_state, monkeypatch):
    alerts = []
    monkeypatch.setattr(main, "configured_platforms", lambda: {"ig"})
    monkeypatch.setattr(main.notify, "admin", lambda text, photo_path=None: alerts.append(text))
    monkeypatch.setattr(main, "get_platform", lambda c: pytest.fail("не должен публиковать"))
    main.run(source=str(TEMPLATE), now=dt.datetime(2026, 10, 22, 10, 0, tzinfo=TZ), only="word-mistake")
    assert any("не опубликован" in a for a in alerts)
