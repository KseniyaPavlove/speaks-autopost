"""python -m agent run | check | list"""
import argparse
import datetime as dt

from . import main, queue
from .config import TZ, env


def cli():
    ap = argparse.ArgumentParser(prog="agent", description="Агент автопубликации S.peaks")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="превью + публикация того, чему пришло время")
    r.add_argument("--dry-run", action="store_true", help="ничего не публиковать, только показать")
    r.add_argument("--now", help="представить, что сейчас это время (ГГГГ-ММ-ДДTЧЧ:ММ)")
    r.add_argument("--only", help="обработать только пост с этим id")
    r.add_argument("--source", help="CSV-файл или ссылка вместо QUEUE_URL")
    c = sub.add_parser("check", help="проверить ключи и таблицу")
    c.add_argument("--source")
    ls = sub.add_parser("list", help="показать, как агент понял таблицу")
    ls.add_argument("--source")
    a = ap.parse_args()

    if a.cmd == "run":
        now = dt.datetime.fromisoformat(a.now).replace(tzinfo=TZ) if a.now else None
        dry = a.dry_run or env("DRY_RUN", "").lower() in ("1", "true")
        main.run(source=a.source, dry_run=dry, now=now, only=a.only)
    elif a.cmd == "check":
        print(main.check(a.source))
    else:
        for p in queue.load(a.source or env("QUEUE_URL")):
            when = f"{p.when:%d.%m %H:%M}" if p.when else "??"
            flag = "✅" if p.ready else "· "
            print(f"{flag} [{p.sheet} #{p.line}] {when}  {p.format or '?':<8} {','.join(p.platforms):<14} "
                  f"figma:{len(p.figma)} drive:{len(p.drive)}  {p.title[:40]}  "
                  + ("ОШИБКИ: " + "; ".join(p.errors) if p.errors else ""))


if __name__ == "__main__":
    cli()
