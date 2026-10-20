"""Google Таблица контент-плана через сервисный аккаунт: читаем вкладки, пишем статус и ссылки."""
import json
import re
from urllib.parse import quote

from .config import env

API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive.readonly"]

_session = None


def session():
    """Авторизованная сессия Google (сервисный аккаунт из секрета GOOGLE_SA_JSON)."""
    global _session
    if _session is None:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        raw = env("GOOGLE_SA_JSON")
        if not raw:
            raise RuntimeError("не задан GOOGLE_SA_JSON — ключ сервисного аккаунта Google")
        creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
        _session = AuthorizedSession(creds)
    return _session


def col_letter(idx: int) -> str:
    s, n = "", idx + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class Sheet:
    def __init__(self, url: str):
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
        if not m:
            raise ValueError(f"не похоже на ссылку на Google-таблицу: {url}")
        self.id = m.group(1)
        only = env("SHEET_TABS")  # необязательно: названия вкладок через запятую
        self.only = [t.strip() for t in only.split(",")] if only else None

    def _get(self, url, **params):
        r = session().get(url, params=params, timeout=120)
        if r.status_code == 403:
            raise RuntimeError("нет доступа к таблице — откройте её для сервисного аккаунта (Редактор)")
        r.raise_for_status()
        return r.json()

    def tables(self):
        """[(название вкладки, строки из Cell)] — только вкладки, похожие на контент-план."""
        from .queue import Cell
        meta = self._get(f"{API}/{self.id}", fields="sheets.properties(title,hidden)")
        titles = [s["properties"]["title"] for s in meta.get("sheets", [])
                  if not s["properties"].get("hidden")]
        if self.only:
            titles = [t for t in titles if t in self.only]
        fields = ("sheets(properties(title),data(rowData(values("
                  "formattedValue,hyperlink,textFormatRuns(format(link(uri))),"
                  "chipRuns(chip(richLinkProperties(uri)))))))")
        out = []
        for title in titles:
            data = self._get(f"{API}/{self.id}", ranges=f"'{title}'!A1:AZ2000", fields=fields)
            grid = (data.get("sheets") or [{}])[0].get("data") or [{}]
            rows = []
            for rd in grid[0].get("rowData", []):
                row = []
                for v in rd.get("values", []):
                    links = []
                    if v.get("hyperlink"):
                        links.append(v["hyperlink"])
                    for run in v.get("textFormatRuns", []):
                        u = run.get("format", {}).get("link", {}).get("uri")
                        if u and u not in links:
                            links.append(u)
                    for run in v.get("chipRuns", []):
                        u = run.get("chip", {}).get("richLinkProperties", {}).get("uri")
                        if u and u not in links:
                            links.append(u)
                    row.append(Cell(text=v.get("formattedValue", ""), links=links))
                rows.append(row)
            out.append((title, rows))
        return out

    def write(self, tab: str, line: int, values: dict):
        """values: {индекс колонки: текст}. Пишет в строку line (нумерация как в таблице)."""
        data = [{"range": f"'{tab}'!{col_letter(j)}{line}", "values": [[v]]} for j, v in values.items()]
        r = session().post(f"{API}/{self.id}/values:batchUpdate",
                           json={"valueInputOption": "USER_ENTERED", "data": data}, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"не удалось записать в таблицу: {r.text[:200]}")


def write_back(post, status: str | None = None, links: list | None = None, old_links: str = ""):
    """Отмечает результат публикации в контент-плане. Без сервисного аккаунта — ничего не делает."""
    src = env("QUEUE_URL", "")
    if not (env("GOOGLE_SA_JSON") and src.startswith("http")):
        return False
    values = {}
    if status and "status" in post.columns:
        values[post.columns["status"]] = status
    if links and "links" in post.columns:
        merged = "\n".join([x for x in [old_links.strip()] if x] + links)
        values[post.columns["links"]] = merged
    if values:
        Sheet(src).write(post.sheet, post.line, values)
    return True


def drive_download(file_id: str, dest):
    """Скачивание файла с Google Drive от имени сервисного аккаунта (если ему открыт доступ)."""
    r = session().get(f"https://www.googleapis.com/drive/v3/files/{quote(file_id)}",
                      params={"alt": "media", "supportsAllDrives": "true"}, stream=True, timeout=600)
    if r.status_code >= 400:
        raise RuntimeError(f"Drive {r.status_code}: нет доступа к файлу")
    with open(dest, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    return dest
