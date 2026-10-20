"""Помощник получения ключей. Запускается один раз на своём компьютере:

    python tools/get_tokens.py instagram
    python tools/get_tokens.py youtube
    python tools/get_tokens.py tiktok
    python tools/get_tokens.py vk

Скрипт подскажет, что открыть в браузере, и напечатает значения для GitHub Secrets.
Нужен только пакет requests (pip install requests).
"""
import base64
import hashlib
import secrets
import sys
import webbrowser
from urllib.parse import parse_qs, urlencode, urlparse

import requests


def ask(prompt):
    return input(f"{prompt}: ").strip()


def code_from(url_or_code):
    if "://" not in url_or_code:
        return {"code": url_or_code}
    q = parse_qs(urlparse(url_or_code).query)
    return {k: v[0] for k, v in q.items()}


def show(pairs):
    print("\n=== Добавьте в GitHub → Settings → Secrets and variables → Actions ===")
    for k, v in pairs.items():
        print(f"{k} = {v}")
    print("=====================================================================\n")


def instagram():
    print("1) В developers.facebook.com откройте своё приложение → Tools → Graph API Explorer.\n"
          "2) Выберите приложение, User Token, отметьте права: instagram_basic, instagram_content_publish,\n"
          "   pages_show_list, pages_read_engagement, business_management → Generate Access Token.")
    app_id, app_secret = ask("App ID"), ask("App Secret")
    short = ask("Скопированный User Token")
    ver = "v26.0"
    ll = requests.get(f"https://graph.facebook.com/{ver}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
        "fb_exchange_token": short}).json()
    if "access_token" not in ll:
        sys.exit(f"Ошибка: {ll}")
    pages = requests.get(f"https://graph.facebook.com/{ver}/me/accounts",
                         params={"access_token": ll["access_token"]}).json().get("data", [])
    if not pages:
        sys.exit("Не нашлось страниц Facebook. Instagram должен быть привязан к странице.")
    for i, p in enumerate(pages):
        print(f"[{i}] {p['name']}")
    page = pages[int(ask("Номер страницы, к которой привязан Instagram") or 0)]
    ig = requests.get(f"https://graph.facebook.com/{ver}/{page['id']}",
                      params={"fields": "instagram_business_account{username}",
                              "access_token": page["access_token"]}).json()
    acc = ig.get("instagram_business_account")
    if not acc:
        sys.exit(f"К странице не привязан профессиональный Instagram: {ig}")
    print(f"Instagram: @{acc.get('username')}. Ключ страницы бессрочный.")
    show({"IG_USER_ID (variable)": acc["id"], "IG_ACCESS_TOKEN (secret)": page["access_token"]})


def youtube():
    print("Google Cloud Console → APIs & Services: включите YouTube Data API v3, создайте OAuth client\n"
          "типа «Desktop app». На экране OAuth consent screen переведите приложение в статус\n"
          "«In production» — иначе ключ умрёт через 7 дней.")
    cid, csec = ask("Client ID"), ask("Client secret")
    redirect = "http://localhost"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
        "access_type": "offline", "prompt": "consent"})
    print(f"\nОткройте и войдите в аккаунт канала:\n{url}\n"
          "Браузер перекинет на localhost и покажет ошибку — это нормально. Скопируйте адрес из строки браузера.")
    webbrowser.open(url)
    code = code_from(ask("Адрес страницы после входа"))["code"]
    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code, "client_id": cid, "client_secret": csec, "redirect_uri": redirect,
        "grant_type": "authorization_code"}).json()
    if "refresh_token" not in tok:
        sys.exit(f"Ошибка: {tok}")
    show({"YT_CLIENT_ID": cid, "YT_CLIENT_SECRET": csec, "YT_REFRESH_TOKEN": tok["refresh_token"]})


def tiktok():
    print("developers.tiktok.com → ваше приложение: продукты Login Kit + Content Posting API (Direct Post),\n"
          "права video.publish, video.upload, user.info.basic. Redirect URI — например страница вашего сайта.")
    key, sec, redirect = ask("Client key"), ask("Client secret"), ask("Redirect URI (точно как в настройках)")
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
        "client_key": key, "response_type": "code", "redirect_uri": redirect,
        "scope": "user.info.basic,video.publish,video.upload", "state": secrets.token_hex(8)})
    print(f"\nОткройте, войдите в аккаунт школы и разрешите доступ:\n{url}")
    webbrowser.open(url)
    code = code_from(ask("Адрес страницы, куда вас перекинуло"))["code"]
    tok = requests.post("https://open.tiktokapis.com/v2/oauth/token/", data={
        "client_key": key, "client_secret": sec, "code": code, "grant_type": "authorization_code",
        "redirect_uri": redirect}).json()
    if "refresh_token" not in tok:
        sys.exit(f"Ошибка: {tok}")
    show({"TT_CLIENT_KEY": key, "TT_CLIENT_SECRET": sec, "TT_REFRESH_TOKEN": tok["refresh_token"]})


def vk():
    print("id.vk.com → «Мои приложения» → создайте Web-приложение, укажите Доверенный Redirect URL\n"
          "(например страница вашего сайта). Вход нужен от аккаунта АДМИНИСТРАТОРА сообщества.")
    cid, redirect = ask("ID приложения"), ask("Redirect URL (точно как в настройках)")
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://id.vk.com/authorize?" + urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": redirect, "state": state,
        "scope": "wall photos video groups", "code_challenge": challenge, "code_challenge_method": "S256"})
    print(f"\nОткройте и разрешите доступ:\n{url}")
    webbrowser.open(url)
    q = code_from(ask("Адрес страницы, куда вас перекинуло"))
    tok = requests.post("https://id.vk.com/oauth2/auth", data={
        "grant_type": "authorization_code", "code": q["code"], "code_verifier": verifier,
        "client_id": cid, "device_id": q.get("device_id", ""), "redirect_uri": redirect, "state": state}).json()
    if "refresh_token" not in tok:
        sys.exit(f"Ошибка: {tok}")
    show({"VK_ID_CLIENT_ID (variable)": cid, "VK_DEVICE_ID": q.get("device_id", ""),
          "VK_REFRESH_TOKEN": tok["refresh_token"]})
    print("VK ID меняет refresh-ключ при каждом обновлении — агент сохранит новый сам, если задан GH_PAT.")


if __name__ == "__main__":
    cmds = {"instagram": instagram, "youtube": youtube, "tiktok": tiktok, "vk": vk}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]]()
