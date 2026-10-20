"""Сохраняем обновлённые токены (TikTok, VK ID) обратно в GitHub Secrets."""
import os
import subprocess

from .config import env


def persist(name: str, value: str) -> bool:
    os.environ[name] = value  # для текущего запуска
    pat, repo = env("GH_PAT"), env("GITHUB_REPOSITORY")
    if not (pat and repo):
        print(f"⚠️ {name} обновился, но GH_PAT не задан — сохраните новое значение вручную")
        return False
    r = subprocess.run(["gh", "secret", "set", name, "--repo", repo, "--body", value],
                       env={**os.environ, "GH_TOKEN": pat}, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"⚠️ не удалось сохранить {name}: {r.stderr.strip()}")
        return False
    return True
