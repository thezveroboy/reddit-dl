# reddit-dl

Downloads images and videos from any public Reddit community
into a folder named after that community.

Needs Python and one package: requests.

Install:

    pip install -r requirements.txt


HOW TO RUN

    python reddit_dl.py LINK_TO_COMMUNITY

Examples:

    python reddit_dl.py https://www.reddit.com/r/pics/
    python reddit_dl.py r/EarthPorn --limit 500
    python reddit_dl.py pics --limit 0 --sort new --since 2024-01-01
    python reddit_dl.py r/pics --days 30

Result: a folder pics (or another community name) appears next to
the script, with files like 1wqh4c4_post_title.jpeg and a post
list _posts.jsonl.


MAIN OPTIONS

  --limit NUMBER ......... how many posts to scan (default 500).
                           0 means everything Reddit gives back (about 1000).
  --sort new|hot|top ..... sorting (default hot).
  --time week|month ...... time window for --sort top.
  --since YYYY-MM-DD .... only posts newer than this date.
  --days NUMBER .......... only posts from the last N days.
  --out FOLDER ........... where to put the community folder (default current).
  --cookies FILE ......... browser cookies file (see below).
  --rss .................. use RSS only (25 newest posts).


COOKIES FILE — FOR FULL ACCESS

Without cookies the script gets only the 25 newest posts: Reddit
cuts off anonymous requests. To download hundreds of posts and go
deeper by date, a cookies file from a browser logged into Reddit
is needed (no approvals or requests required, a normal login is
enough).

How to get the file:

  1. Install the Get cookies.txt LOCALLY extension
     (available for Chrome and Firefox).
  2. Open a reddit.com tab.
  3. Click the extension icon, click Export.
  4. Put the downloaded cookies.txt next to the script.

Run with cookies:

    python reddit_dl.py r/pics --cookies cookies.txt --limit 500


NOTES

  Reddit videos download without sound (that is how Reddit stores them).
  External sites download only by direct file link.
  Albums without a direct link are skipped.


================================================================


# reddit-dl

Скрипт качает картинки и видео из любого открытого паблика Реддита
в папку с именем этого паблика.

Что нужно: Python и один пакет requests.

Установка:

    pip install -r requirements.txt


КАК ЗАПУСКАТЬ

    python reddit_dl.py ССЫЛКА_НА_ПАБЛИК

Примеры:

    python reddit_dl.py https://www.reddit.com/r/pics/
    python reddit_dl.py r/EarthPorn --limit 500
    python reddit_dl.py pics --limit 0 --sort new --since 2024-01-01
    python reddit_dl.py r/pics --days 30

Результат: рядом со скриптом появится папка pics (или другое имя
паблика), в ней файлы вида 1wqh4c4_название_поста.jpeg и список
постов _posts.jsonl.


ОСНОВНЫЕ КЛЮЧИ

  --limit ЧИСЛО ......... сколько постов перебрать (по умолч. 500).
                          0 — всё, что отдаст Реддит (около 1000).
  --sort new|hot|top .... сортировка (по умолч. hot).
  --time week|month ..... окно времени для --sort top.
  --since ГГГГ-ММ-ДД .... качать только посты новее этой даты.
  --days ЧИСЛО .......... качать только посты за последние N дней.
  --out ПАПКА ........... куда класть папку паблика (по умолч. текущая).
  --cookies ФАЙЛ ........ файл кукис из браузера (см. ниже).
  --rss ................. брать только через RSS (25 самых новых).


ФАЙЛ КУКИС — ДЛЯ ПОЛНОГО ДОСТУПА

Без кукис скрипт берёт только 25 самых новых постов: Реддит режет
анонимные запросы. Чтобы качать сотни постов и глубже по дате,
нужен файл кукис из браузера, в котором выполнен вход в Реддит
(одобрение и заявки не нужны, хватает обычного входа).

Как получить файл:

  1. Поставь расширение Get cookies.txt LOCALLY
     (есть для Хрома и Фаерфокса).
  2. Открой вкладку reddit.com.
  3. Нажми значок расширения, нажми Export.
  4. Скачанный cookies.txt положи в папку со скриптом.

Запуск с кукис:

    python reddit_dl.py r/pics --cookies cookies.txt --limit 500


ЗАМЕЧАНИЯ

  Видео с Реддита качается без звука (так его хранит сам Реддит).
  С внешних сайтов качаются только прямые ссылки на файл.
  Альбомы без прямой ссылки пропускаются.
