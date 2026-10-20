"""Журнал: что уже опубликовано, какие тексты сгенерированы, какие превью отправлены."""
import datetime as dt
import json

from .config import STATE_DIR


class State:
    def __init__(self, path=None):
        self.path = path or STATE_DIR / "published.json"
        self.captions_dir = self.path.parent / "captions"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}
        self.data.setdefault("posts", {})
        self.data.setdefault("flags", {})

    # --- публикации ---
    def entry(self, post_id, platform) -> dict:
        return self.data["posts"].get(post_id, {}).get(platform, {})

    def status(self, post_id, platform):
        return self.entry(post_id, platform).get("status")

    def attempts(self, post_id, platform) -> int:
        return self.entry(post_id, platform).get("attempts", 0)

    def record(self, post_id, platform, status, **extra):
        rec = self.data["posts"].setdefault(post_id, {}).setdefault(platform, {})
        if status == "error":
            rec["attempts"] = rec.get("attempts", 0) + 1
        rec.update(status=status, at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **extra)

    # --- разовые флаги (чтобы не спамить уведомлениями) ---
    def flag(self, key) -> bool:
        return key in self.data["flags"]

    def set_flag(self, key):
        self.data["flags"][key] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    # --- кэш адаптированных текстов ---
    def captions(self, post_id, fingerprint):
        f = self.captions_dir / f"{post_id}.json"
        try:
            cached = json.loads(f.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return cached.get("texts") if cached.get("fingerprint") == fingerprint else None

    def save_captions(self, post_id, fingerprint, texts):
        self.captions_dir.mkdir(parents=True, exist_ok=True)
        (self.captions_dir / f"{post_id}.json").write_text(
            json.dumps({"fingerprint": fingerprint, "texts": texts}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
