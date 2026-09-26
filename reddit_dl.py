#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reddit-dl — скачать все изображения (+видео) паблика Reddit в подпапку с именем паблика.

Использование:
    python reddit_dl.py <ссылка_на_паблик | r/имя | имя> [--limit N] [--sort new|hot|top|rising]
                     [--time hour|day|week|month|year|all] [--since ГГГГ-ММ-ДД | --days N]
                     [--out КАТАЛОГ] [--delay СЕК]
                     [--client-id ID --client-secret KEY | --cookies cookies.txt]

Примеры:
    python reddit_dl.py https://www.reddit.com/r/EarthPorn/
    python reddit_dl.py r/pics --limit 500
    python reddit_dl.py pics --limit 0 --sort new --since 2024-01-01
    python reddit_dl.py r/wallpapers --limit 100 --sort top --time month
    python reddit_dl.py r/nsfw --client-id XXX --client-secret YYY --limit 1000

Откуда берутся посты (порядок попыток):
  1. Cookies из твоего браузера (--cookies cookies.txt) — ТЫ УЖЕ ЗАЛОГИНЕН,
     поэтому JSON работает полностью (~1000 постов), без заявок и одобрений.
     Экспорт: расширение "Get cookies.txt LOCALLY" → открыть reddit.com →
     Export → файл рядом со скриптом. Основной рабочий путь с 2025 года.
  2. OAuth (если даны --client-id/--client-secret или env REDDIT_CLIENT_ID /
     REDDIT_CLIENT_SECRET) — только для СТАРЫХ приложений, созданных до
     ноября 2025. Новые Reddit не даёт: кнопка "create app" отвечает
     Responsible Builder Policy и требует заявки с одобрением (часто отказ).
  3. Анонимный публичный JSON (/r/xxx/.json без ключей). С жилых IP обычно
     работает, с дата-центров/VPN Reddit часто отвечает 403 — это их
     антибот-защита, а не баг скрипта.
  4. RSS (/r/xxx/new.rss) — работает почти всегда, но отдаёт только ~25 самых
     новых постов без пагинации. Скрипт сам упадёт на RSS, если JSON закрыт.

Зависимость только `requests` (pip install -r requirements.txt).
Уважает прокси из окружения (HTTP_PROXY/HTTPS_PROXY, в т.ч. socks5 Tor).

Ограничения честно:
- Листинг Reddit отдаёт максимум ~1000 последних постов.
- Рейт-лимит анонимного JSON ~10 запр/мин: паузы уже вшиты, при 429 ждём.
- Фильтр по дате (--since/--days): при --sort new останавливается рано
  (лента хронологическая); при hot/top/rising старые посты просто
  пропускаются, листинг идёт дальше до --limit.
- Видео v.redd.it скачивается как fallback_mp4 без звука (Reddit хранит
  звук отдельной дорожкой; склейка — через ffmpeg вручную).
- Внешние хосты: качаются только ПРЯМЫЕ ссылки на файл
  (i.imgur.com/xxx.jpg, *.mp4 и т.п.). Альбомы imgur / redgifs / gifv
  без прямого URL пропускаются (в логе видно как skip).
"""

import argparse
import base64
import datetime as dt
import html
import http.cookiejar
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse, unquote

try:
    import requests
except ImportError:
    sys.exit("Нужен пакет requests: pip install -r requirements.txt")

# Reddit режет дефолтные/ботовые UA (429/403), браузерный проходит чаще.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
VID_EXTS = (".mp4", ".webm", ".mov", ".mkv")
ALL_EXTS = IMG_EXTS + VID_EXTS

# прямые картинки/видео с этих хостов качаем, даже если URL без расширения
DIRECT_HOSTS = ("i.redd.it", "preview.redd.it", "i.imgur.com", "i.redgifs.com")


def parse_target(target):
    """Из ссылки/короткой записи вернуть (subreddit, sort из URL|None)."""
    t = target.strip()
    m = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)(?:/(hot|new|top|rising|controversial))?", t, re.I)
    if m:
        return m.group(1), (m.group(2) or "").lower() or None
    m = re.match(r"r/([A-Za-z0-9_]+)$", t, re.I)
    if m:
        return m.group(1), None
    m = re.match(r"([A-Za-z0-9_]+)$", t)
    if m:
        return m.group(1), None
    raise SystemExit(f"Не понял паблик из аргумента: {target!r}. Пример: https://www.reddit.com/r/pics/ или r/pics")


def parse_since(since_str, days):
    if since_str:
        try:
            d = dt.datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            raise SystemExit("--since ждёт дату вида ГГГГ-ММ-ДД, например 2024-01-01")
        return d.timestamp()
    if days is not None:
        if days < 0:
            raise SystemExit("--days не может быть отрицательным")
        now = dt.datetime.now(dt.timezone.utc)
        return (now - dt.timedelta(days=days)).timestamp()
    return 0.0


def clean_url(u):
    """Убрать amp-обёртки, вернуть URL файла.

    Бонус: preview.redd.it (превьюшки, часто 403 при скачивании) меняем на
    i.redd.it — тот же файл в полном размере, отдаётся без блокировок.
    Исключение: external-preview.redd.it — у него подпись доступа лежит
    в query (?s=...), её срезать НЕЛЬЗЯ, иначе будет 403.
    """
    u = html.unescape(html.unescape(u or "")).replace("&amp;", "&").strip().rstrip('";')
    if u.startswith("//"):
        u = "https:" + u
    host = urlparse(u).netloc
    if host == "preview.redd.it":
        u = u.replace("://preview.redd.it/", "://i.redd.it/", 1)
        u = u.split("?")[0].split("#")[0]
    elif host != "external-preview.redd.it":
        u = u.split("?")[0].split("#")[0]
    else:
        u = u.split("#")[0]
    return u


def ext_of(url, content_type=""):
    path = unquote(urlparse(url).path or "")
    ext = Path(path).suffix.lower()
    if ext in ALL_EXTS:
        return ext
    # preview.redd.it иногда без расширения — смотрим Content-Type при скачивании
    ct = (content_type or "").lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    if "mp4" in ct:
        return ".mp4"
    if "webm" in ct:
        return ".webm"
    return ""


def is_direct_file(url):
    low = clean_url(url).lower()
    # расширение смотрим по пути (без query: у external-preview там подпись)
    if unquote(urlparse(low).path or "").endswith(ALL_EXTS):
        return True
    host = urlparse(low).netloc
    return host in DIRECT_HOSTS


def media_urls(post):
    """Все скачиваемые URL поста: [(url, kind)], kind = image|video."""
    out = []

    # 1. Галереи Reddit: порядок из gallery_data, байты из media_metadata
    if post.get("is_gallery"):
        meta = post.get("media_metadata") or {}
        order = []
        try:
            order = [x["media_id"] for x in (post.get("gallery_data") or {}).get("items", []) if "media_id" in x]
        except (AttributeError, TypeError):
            order = []
        for mid in order or list(meta.keys()):
            m = meta.get(mid) or {}
            s = m.get("s") or {}
            u = clean_url(s.get("u") or s.get("gif") or "")
            if u:
                out.append((u, "video" if m.get("e") == "AnimatedImage" and u.endswith(".mp4") else "image"))
        if out:
            return out

    # 2. Видео Reddit
    media = post.get("media") or post.get("secure_media") or {}
    rv = (media.get("reddit_video") or {})
    if rv.get("fallback_url"):
        out.append((clean_url(rv["fallback_url"]), "video"))
        return out

    # 3. Репост чужого поста с картинкой — тянем медиа оригинала
    xlist = post.get("crosspost_parent_list") or []
    if xlist and isinstance(xlist[0], dict):
        sub = media_urls(xlist[0])
        if sub:
            return sub

    url = (post.get("url_overridden_by_dest") or post.get("url") or "").strip()
    cu = clean_url(url)
    if cu.lower().endswith(".gifv"):
        # imgur gifv = тот же id как .mp4 (проверяем ДО is_direct_file,
        # иначе DIRECT_HOSTS проглотит .gifv как есть)
        out.append((cu[:-5] + ".mp4", "video"))
        return out
    if url and not (url.startswith("/r/") or ("/comments/" in url and "reddit.com" in url)):
        if is_direct_file(url):
            kind = "video" if unquote(urlparse(cu.lower()).path or "").endswith(VID_EXTS) else "image"
            out.append((cu, kind))
            return out

    # 4. Последний шанс: превью Reddit (single-image посты с внешних хостов)
    try:
        prev = (post.get("preview") or {}).get("images") or []
        src = (prev[0].get("source") or {}).get("url") if prev else None
        if src:
            src = clean_url(src)
            if is_direct_file(src):
                out.append((src, "image"))
                return out
    except (AttributeError, IndexError, TypeError):
        pass

    # 5. Ссылки на файлы прямо в тексте поста (selftext): например пост
    # "смотрите: https://d.l3n.co/xxx.jpeg" — поле url у него пустое,
    # а браузер такую ссылку открывает. Забираем все прямые файлы из текста.
    text = (post.get("selftext") or "")
    if text:
        for m in re.findall(r"https?://[^\s)\"'<>]+", text):
            u = clean_url(html.unescape(m).rstrip(".,;!"))
            if is_direct_file(u) and all(u != x[0] for x in out):
                kind = "video" if unquote(urlparse(u.lower()).path or "").endswith(VID_EXTS) else "image"
                out.append((u, kind))
        if out:
            return out

    return out


# ---------- источники листинга ----------

def load_cookies(session, path):
    jar = http.cookiejar.MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except FileNotFoundError:
        raise SystemExit(f"Файл cookies не найден: {path} (экспорт из браузера в формате Netscape)")
    except Exception as e:
        raise SystemExit(f"Не смог прочитать cookies {path}: {e}")
    session.cookies.update(requests.utils.cookiejar_from_dict({c.name: c.value for c in jar}))
    print(f"  cookies: подхватил {len(jar)} шт. из {path}")


def oauth_token(session, client_id, client_secret):
    """Application-only токен (без логина): grant_type=client_credentials."""
    cred = base64.b64encode(f"{client_id}:".encode()).decode()  # секрет не нужен для чтения
    # вообще-то Reddit требует secret у script-приложений; пробуем оба варианта
    for secret in (client_secret, ""):
        cred = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        try:
            r = session.post("https://www.reddit.com/api/v1/access_token",
                             headers={"Authorization": f"Basic {cred}", "User-Agent": UA},
                             data={"grant_type": "client_credentials",
                                   "device_id": "DO-NOT-TRACK-REDDIT-DL-01"},
                             timeout=30)
        except requests.RequestException as e:
            print(f"  OAuth: сеть: {e}")
            continue
        if r.status_code == 200:
            try:
                tok = r.json()["access_token"]
                print("  OAuth: токен получен (application-only)")
                return tok
            except (ValueError, KeyError):
                pass
        print(f"  OAuth: HTTP {r.status_code}: {r.text[:150]}")
    raise SystemExit("OAuth не выдал токен. Проверь --client-id/--client-secret "
                     "(reddit.com/prefs/apps -> create app -> тип script).")


def fetch_json_page(session, base, sub, sort, after, t):
    """Одна страница JSON-листинга. Возвращает data или бросает RedditBlocked."""
    params = {"limit": "100", "raw_json": "1"}
    if after:
        params["after"] = after
    if sort == "top":
        params["t"] = t
    url = f"{base}/r/{sub}/{sort}.json"
    try:
        r = session.get(url, params=params, timeout=30,
                        headers={"Accept": "application/json"})
    except requests.RequestException as e:
        raise RedditBlocked(f"сеть: {e}")
    if r.status_code == 429:
        raise RedditBlocked("429")  # обработает вызывающий (пауза+репит)
    if r.status_code == 403:
        raise RedditBlocked("403: Reddit закрыл анонимный JSON с этого IP")
    if r.status_code == 404:
        raise SystemExit(f"r/{sub} не найден (404). Проверь имя паблика.")
    if r.status_code != 200:
        raise RedditBlocked(f"HTTP {r.status_code}")
    try:
        return r.json()["data"]
    except (ValueError, KeyError):
        raise RedditBlocked("не-JSON в ответе")


class RedditBlocked(Exception):
    pass


def fetch_rss(session, sub, limit):
    """RSS-лента: только ~25 самых новых, без пагинации. Возвращает список постов."""
    r = session.get(f"https://www.reddit.com/r/{sub}/new.rss",
                    params={"limit": str(min(limit, 100))}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"RSS тоже закрыт: HTTP {r.status_code}")
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        raise SystemExit(f"RSS не распарсился: {e}")
    ns = {"a": "http://www.w3.org/2005/Atom",
          "m": "http://search.yahoo.com/mrss/"}
    posts = []
    for e in root.findall("a:entry", ns):
        link = (e.find("a:link", ns) or {}).get("href", "")
        m = re.search(r"/comments/([a-z0-9]+)/", link)
        pid = m.group(1) if m else (e.findtext("a:id", "", ns) or "").strip().split("_")[-1]
        title = e.findtext("a:title", "", ns) or pid
        upd = e.findtext("a:updated", "", ns) or ""
        try:
            created = dt.datetime.fromisoformat(upd).timestamp()
        except ValueError:
            created = 0
        urls = []
        thumb = e.find("m:thumbnail", ns)
        if thumb is not None and thumb.get("url"):
            u = clean_url(thumb.get("url"))
            if is_direct_file(u):
                urls.append((u, "image"))
        content = e.findtext("a:content", "", ns) or ""
        for src in re.findall(r'<img[^>]+src="([^"]+)"', content):
            u = clean_url(src)
            if is_direct_file(u) and all(u != x[0] for x in urls):
                urls.append((u, "image"))
        posts.append({"id": pid, "title": title, "author": "", "created_utc": created,
                      "permalink": urlparse(link).path, "_urls": urls})
    return posts


# ---------- скачивание ----------

def sanitize(name, n=60):
    name = re.sub(r"[\\/:*?\"<>|]", "_", name or "").strip()
    name = re.sub(r"\s+", " ", name)
    return name[:n].rstrip(" .") or "untitled"


def download(session, url, dest_path, delay, tries=3):
    """Скачать файл с повторами: медленные хосты (l3n.co и т.п.) часто
    отваливаются по таймауту с первого раза, со второго-третьего отдают."""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        return "exists"
    last = "?"
    for attempt in range(tries):
        try:
            with session.get(url, stream=True, timeout=120,
                             headers={"Referer": "https://www.reddit.com/"}) as r:
                if r.status_code != 200:
                    last = f"HTTP {r.status_code}"
                    time.sleep(3)
                    continue
                # уточнить расширение по Content-Type, если URL без него
                if not dest_path.suffix or dest_path.suffix == ".bin":
                    e = ext_of(url, r.headers.get("Content-Type", ""))
                    if e:
                        dest_path = dest_path.with_suffix(e)
                        if dest_path.exists() and dest_path.stat().st_size > 0:
                            return "exists"
                tmp = dest_path.with_suffix(dest_path.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(256 * 1024):
                        if chunk:
                            f.write(chunk)
                tmp.rename(dest_path)
                if delay:
                    time.sleep(delay)
                return "ok:" + dest_path.name
        except requests.RequestException as e:
            last = f"сеть: {e}"
            time.sleep(5 + attempt * 5)
    return last


def handle_post(session, p, urls, dest, manifest, delay):
    """Скачать файлы одного поста. Возвращает (saved, skipped)."""
    saved, skipped = 0, 0
    pid = p.get("id") or "post"
    title = sanitize(p.get("title") or pid)
    manifest.write(json.dumps({"id": pid, "title": p.get("title"),
                               "author": p.get("author"), "created_utc": p.get("created_utc"),
                               "permalink": p.get("permalink"), "files": [u for u, _ in urls]},
                              ensure_ascii=False) + "\n")
    for i, (u, kind) in enumerate(urls):
        e = ext_of(u) or (".mp4" if kind == "video" else ".bin")
        fname = f"{pid}_{i}_{title}{e}" if len(urls) > 1 else f"{pid}_{title}{e}"
        if len(fname) > 150:  # длинные заголовки
            fname = f"{pid}_{i}{e}"
        res = download(session, u, dest / fname, delay)
        if res.startswith("ok:") or res == "exists":
            saved += 1
        else:
            skipped += 1
            print(f"  skip {pid}: {u} ({res})")
    return saved, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description="Скачать изображения паблика Reddit в подпапку с именем паблика.")
    ap.add_argument("target", help="Ссылка на паблик (https://www.reddit.com/r/pics/) или r/pics или pics")
    ap.add_argument("--limit", type=int, default=500, help="Максимум постов перебрать (0 = всё, пока Reddit отдаёт, ~1000). По умолч. 500")
    ap.add_argument("--sort", default=None, choices=["hot", "new", "top", "rising"],
                    help="Сортировка (по умолч. hot; из URL приоритетнее флаг)")
    ap.add_argument("--time", default="all", choices=["hour", "day", "week", "month", "year", "all"],
                    help="Окно для --sort top (по умолч. all)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--since", default=None, help="Качать посты новее даты ГГГГ-ММ-ДД (от текущей назад)")
    g.add_argument("--days", type=int, default=None, help="Качать посты за последние N дней")
    ap.add_argument("--out", default=".", help="Куда класть подпапку паблика (по умолч. текущая папка)")
    ap.add_argument("--delay", type=float, default=0.4, help="Пауза между скачиваниями файлов, сек (по умолч. 0.4)")
    ap.add_argument("--client-id", default=os.environ.get("REDDIT_CLIENT_ID"),
                    help="ID приложения (или env REDDIT_CLIENT_ID). Надёжный путь при 403")
    ap.add_argument("--client-secret", default=os.environ.get("REDDIT_CLIENT_SECRET"),
                    help="Секрет приложения (или env REDDIT_CLIENT_SECRET)")
    ap.add_argument("--cookies", default=None, help="cookies.txt из браузера (Netscape) — открывает NSFW/приват и снимает 403")
    ap.add_argument("--rss", action="store_true", help="Сразу брать через RSS (~25 новых, без пагинации)")
    a = ap.parse_args(argv)

    sub, url_sort = parse_target(a.target)
    sort = a.sort or url_sort or "hot"
    since_ts = parse_since(a.since, a.days)
    want_all = (a.limit or 0) <= 0
    limit = 10 ** 9 if want_all else a.limit

    dest = Path(a.out) / sub
    dest.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    if a.cookies:
        load_cookies(session, a.cookies)

    use_oauth = bool(a.client_id and a.client_secret)
    if use_oauth:
        tok = oauth_token(session, a.client_id, a.client_secret)
        session.headers.update({"Authorization": f"bearer {tok}"})
        base = "https://oauth.reddit.com"
    else:
        base = "https://www.reddit.com"

    print(f"Паблик: r/{sub} | sort={sort} | limit={'всё' if want_all else limit}"
          + (f" | новее {a.since or str(a.days) + ' дн.'}" if since_ts else "")
          + f" | папка: {dest}"
          + (" | OAuth" if use_oauth else ""))
    if sort != "new" and since_ts:
        print("Внимание: лента не хронологическая — старые посты пропускаю, но листинг иду до конца (ранней остановки не будет).")

    seen = saved = skipped = 0
    manifest = open(dest / "_posts.jsonl", "a", encoding="utf-8")

    def rss_run():
        nonlocal seen, saved, skipped
        print("Режим RSS: только ~25 самых новых постов, пагинации нет.")
        for p in fetch_rss(session, sub, limit):
            if seen >= limit:
                break
            seen += 1
            if since_ts and (p.get("created_utc") or 0) < since_ts:
                skipped += 1
                break  # RSS хронологический — дальше только старее
            urls = p.pop("_urls")
            if not urls:
                skipped += 1
                continue
            s, k = handle_post(session, p, urls, dest, manifest, a.delay)
            saved += s
            skipped += k
            print(f"\rПостов: {seen} | файлов: {saved} | пропущено: {skipped}", end="", flush=True)
        print()

    if a.rss:
        rss_run()
    else:
        after, early_stop, pages = None, False, 0
        while seen < limit:
            try:
                data = fetch_json_page(session, base, sub, sort, after, a.time)
            except RedditBlocked as e:
                if str(e) == "429":
                    print("  [429] лимит, жду 60c…")
                    time.sleep(60)
                    continue
                print(f"  JSON закрыт ({e}).")
                if use_oauth or a.cookies:
                    raise SystemExit("Не помогло даже с авторизацией — Reddit упёрся. Попробуй позже или --rss.")
                print("  Падаю на RSS (только ~25 новых). Для полного доступа: "
                      "--cookies cookies.txt (экспорт из браузера, где ты залогинен).")
                rss_run()
                break
            children = data.get("children") or []
            if not children:
                break
            pages += 1
            for ch in children:
                if seen >= limit:
                    break
                p = ch.get("data") or {}
                seen += 1
                if since_ts and (p.get("created_utc") or 0) < since_ts:
                    skipped += 1
                    if sort == "new":
                        early_stop = True
                        break  # дальше только старее
                    continue
                urls = media_urls(p)
                if not urls:
                    skipped += 1
                    continue
                s, k = handle_post(session, p, urls, dest, manifest, a.delay)
                saved += s
                skipped += k
                print(f"\rПостов: {seen} | файлов: {saved} | пропущено: {skipped}", end="", flush=True)
            print()
            manifest.flush()
            if early_stop:
                print("Дошли до даты --since/--days, останавливаюсь (sort=new).")
                break
            after = data.get("after")
            if not after:
                break
            time.sleep(6 if not use_oauth else 2)  # анонимный лимит ~10/мин

    manifest.close()
    print(f"Готово: r/{sub} -> {dest} | постов просмотрено: {seen}, файлов сохранено: {saved}, пропущено: {skipped}")
    print("Список постов: _posts.jsonl")


if __name__ == "__main__":
    main()
