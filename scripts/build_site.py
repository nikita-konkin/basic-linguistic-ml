"""Собрать сайт курса для GitHub Pages.

    python scripts/build_site.py --output dist
    python scripts/build_site.py --output dist --journal путь/к/journal.json

Сайт статический: главная страница со справкой и материалами и страница
журнала. Что на главной — решает `site/course.toml`; скрипт только
раскладывает файлы и пишет HTML. Сторонних пакетов не нужно.

Файлы курса копируются под латинскими именами (`files/lectures/intro.pdf`),
а ссылка на них несёт атрибут `download` с русским именем. Причина та же, что
в `run_notebooks.py`: часть имён на диске записана в NFD, часть в NFC, и
ссылка с кириллицей в URL на Linux-сборке может не совпасть с файлом.
По той же причине пути из `course.toml` ищутся сравнением имён в NFC.

Журнал хранится не здесь, а в ветке `journal`: workflow достаёт оттуда
`journal.json` и передаёт его через `--journal`. В файле только шифротекст.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import os
import re
import shutil
import sys
import tomllib
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_notebooks import LAB_DIR, find_notebooks  # noqa: E402

REPO_URL = "https://github.com/nikita-konkin/basic-linguistic-ml"
KIT_SLUG = "mlg-labs.zip"
KIT_NAME = "МЛГ_лабораторные.zip"
KIT_FOLDER = "МЛГ_лабораторные"
# Дата внутри архива постоянная: тогда одинаковые материалы дают
# побайтно одинаковый архив, и пересборка ради журнала его не меняет.
ZIP_TIME = (2020, 1, 1, 0, 0, 0)
MOSCOW = dt.timezone(dt.timedelta(hours=3))

KIT_README = """\
Комплект для лабораторных работ курса «Основы машинного обучения»
(магистратура «Цифровая лингвистика и локализация»).

1. Распакуйте архив и открывайте ноутбуки из этой папки: пути к данным
   в них относительные (data/loc_corpus.csv и т. д.).
2. Нужен Python 3.12. Библиотеки:
       pip install -r requirements.txt
   Версии в requirements.txt проверены: на них все работы выполняются
   сверху вниз без ошибок. TensorFlow нужен только Л.р. № 4; если ставить
   его не хочется, удалите строку tensorflow из requirements.txt.
3. Нужен интернет: вводная Л.р. «Классификация» загружает наборы iris и
   penguins через seaborn, а «Классификация текстов» в разделе 13 при первом
   запуске скачивает корпус 20 Newsgroups (около 14 МБ).
4. Л.р. № 4 удобнее всего выполнять в Google Colab со средой выполнения GPU.

Сайт курса: https://konkin-nikita.ru/basic-linguistic-ml/
"""


# ------------------------------------------------------------------ файлы


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def resolve(relative: str) -> Path:
    """Файл по пути из course.toml; каждая часть пути сверяется в NFC."""
    current = ROOT
    for part in Path(relative).parts:
        matches = [name for name in os.listdir(current) if nfc(name) == nfc(part)]
        if not matches:
            raise FileNotFoundError(f"нет файла {relative!r}: в {current} не найдено {part!r}")
        current = current / matches[0]
    return current


def version(path: Path) -> str:
    """Метка содержимого для ссылки `?v=`: после правки браузер не возьмёт
    старый файл из кэша (Pages разрешает кэшировать его 10 минут)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def size_text(path: Path) -> str:
    size = path.stat().st_size
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))} КБ"
    return f"{size / (1024 * 1024):.1f} МБ".replace(".", ",")


def check_labs(labs: list[dict]) -> None:
    """В course.toml должны быть ровно те ноутбуки, что лежат в Л.р/.

    Иначе новая работа молча не попадёт на сайт, а удалённая оставит
    битую ссылку. Список текущих берётся там же, где его берёт прогон
    ноутбуков, — устаревшие версии отсеиваются по LEGACY_MARKERS.
    """
    on_disk = {nfc(name) for name in find_notebooks(with_tensorflow=True)}
    listed = {nfc(Path(lab["file"]).name) for lab in labs}
    if on_disk != listed:
        missing = sorted(on_disk - listed)
        extra = sorted(listed - on_disk)
        raise SystemExit(
            "course.toml расходится с папкой Л.р/:\n"
            f"  не перечислены: {missing}\n  лишние: {extra}"
        )


def copy_materials(course: dict, out: Path) -> dict[str, dict]:
    """Скопировать лекции, ноутбуки и собрать архив; вернуть сведения для ссылок."""
    files: dict[str, dict] = {}
    for kind, folder, ext in (("lectures", "lectures", ".pdf"), ("labs", "labs", ".ipynb")):
        target_dir = out / "files" / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        for item in course.get(kind, []):
            source = resolve(item["file"])
            target = target_dir / f"{item['slug']}{ext}"
            shutil.copyfile(source, target)
            files[f"{kind}:{item['slug']}"] = {
                "href": f"files/{folder}/{target.name}",
                "download": nfc(source.name),
                "size": size_text(source),
            }

    kit = out / "files" / KIT_SLUG
    build_kit(course, kit)
    files["kit"] = {"href": f"files/{KIT_SLUG}", "download": KIT_NAME, "size": size_text(kit)}
    return files


def _add(archive: zipfile.ZipFile, source: Path, arcname: str) -> None:
    info = zipfile.ZipInfo(nfc(arcname), date_time=ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, source.read_bytes())


def build_kit(course: dict, target: Path) -> None:
    """Архив для студента: ноутбуки, data/, requirements.txt и памятка.

    Имена внутри приводятся к NFC: распакованный на Windows или Linux
    комплект иначе получил бы файл, имя которого не совпадает с набранным.
    """
    with zipfile.ZipFile(target, "w") as archive:
        for lab in course["labs"]:
            source = resolve(lab["file"])
            _add(archive, source, f"{KIT_FOLDER}/{source.name}")
        data_dir = LAB_DIR / "data"
        for name in sorted(os.listdir(data_dir), key=nfc):
            path = data_dir / name
            if path.is_file() and not name.startswith("."):
                _add(archive, path, f"{KIT_FOLDER}/data/{name}")
        _add(archive, ROOT / "requirements.txt", f"{KIT_FOLDER}/requirements.txt")
        info = zipfile.ZipInfo(f"{KIT_FOLDER}/КАК_ЗАПУСТИТЬ.txt", date_time=ZIP_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, KIT_README.encode("utf-8"))


# ------------------------------------------------------------------- HTML

e = html.escape
URL = re.compile(r"https?://[^\s<>\"«»]+[^\s<>\"«».,;:!?)]")
TRACKS = ("intro", "main", "demo")


def inline(text: str, links: bool = True) -> str:
    """Экранировать текст, `код` оформить как код, ссылки сделать ссылками."""
    out = e(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    if not links:
        return out
    return URL.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', out)


def paragraphs(text: str) -> str:
    blocks = [" ".join(block.split()) for block in text.strip().split("\n\n") if block.strip()]
    return "\n".join(f"<p>{inline(block)}</p>" for block in blocks)


def plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        word = one
    elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        word = few
    else:
        word = many
    return f"{count} {word}"


def file_row(kind: str, title: str, href: str, size: str = "", download: str = "", desc: str = "") -> str:
    """Строка материала: метка типа, название, размер, действие. Вся строка — ссылка."""
    attrs = f' download="{e(download)}"' if download else ""
    if kind == "web":
        attrs += ' target="_blank" rel="noopener"'
    desc_html = f'<span class="desc">{inline(desc, links=False)}</span>' if desc else ""
    return (
        f'<li><a class="file" href="{e(href)}"{attrs}>'
        f'<span class="chip chip-{kind}">{kind}</span>'
        f'<span class="what">{e(title)}{desc_html}</span>'
        f'<span class="size">{e(size)}</span>'
        f'<span class="go">{"открыть" if kind == "web" else "скачать"}</span></a></li>'
    )


def download_row(kind: str, title: str, info: dict, desc: str = "") -> str:
    return file_row(kind, title, info["href"], info["size"], info["download"], desc)


def check_route(course: dict) -> None:
    """Поля схемы курса в course.toml: ошибка видна при сборке, а не на сайте."""
    lectures = {item["slug"] for item in course.get("lectures", [])}
    playgrounds = {item["url"] for item in course.get("playgrounds", [])}
    slugs = [lab["slug"] for lab in course["labs"]]
    problems = []
    for lab in course["labs"]:
        where = f"[[labs]] {lab['slug']}"
        if lab.get("track", "main") not in TRACKS:
            problems.append(f"{where}: track должен быть одним из {TRACKS}")
        if lab.get("lecture") and lab["lecture"] not in lectures:
            problems.append(f"{where}: нет лекции со slug {lab['lecture']!r}")
        if lab.get("playground") and lab["playground"] not in playgrounds:
            problems.append(f"{where}: площадки {lab['playground']!r} нет в [[playgrounds]]")
        kwic = lab.get("kwic")
        if kwic is not None and (len(kwic) != 3 or not all(isinstance(part, str) for part in kwic)):
            problems.append(f"{where}: kwic — три строки: левый контекст, ключ, правый контекст")
    if course.get("current") and course["current"] not in slugs:
        problems.append(f"current = {course['current']!r}: такой работы нет")
    if problems:
        raise SystemExit("course.toml:\n  " + "\n  ".join(problems))


def selected_work(course: dict) -> str:
    return course.get("current") or course["labs"][0]["slug"]


def station_name(lab: dict) -> str:
    return lab.get("journal") or lab["title"]


def route_svg(labs: list[dict], selected: str) -> str:
    """Схема для широкого экрана: конвейер — сплошная линия, ответвления — пунктир."""
    count = len(labs)
    left, right, main_y, branch_y = 64, 576, 88, 46
    xs = [left + index * (right - left) / max(count - 1, 1) for index in range(count)]
    tracks = [lab.get("track", "main") for lab in labs]
    ys = [branch_y if track == "intro" else main_y for track in tracks]

    lines = []
    for index in range(count - 1):
        x1, y1, x2, y2 = xs[index], ys[index], xs[index + 1], ys[index + 1]
        if tracks[index] == tracks[index + 1] == "main":
            lines.append(f'<line class="rail" x1="{x1:.0f}" y1="{y1}" x2="{x2:.0f}" y2="{y2}" stroke-width="7" stroke-linecap="round"/>')
        else:
            middle = (x1 + x2) / 2
            lines.append(
                f'<path class="branch" d="M{x1:.0f} {y1} C {middle:.0f} {y1} {middle:.0f} {y2} {x2:.0f} {y2}" '
                'stroke-width="5" fill="none" stroke-dasharray="2 8" stroke-linecap="round"/>'
            )

    # Подписи соседних станций основной линии не помещаются рядом, поэтому
    # чередуются: под линией, над линией, под линией...
    stations = []
    main_seen = 0
    for lab, x, y, track in zip(labs, xs, ys, tracks):
        radius = 11 if track == "main" else 9
        name, badge = e(station_name(lab)), e(lab.get("badge", ""))
        below = track == "main" and main_seen % 2 == 0
        if track == "main":
            main_seen += 1
        if below:
            labels = (
                f'<text class="name" x="{x:.0f}" y="{y + 28}" text-anchor="middle">{name}</text>'
                f'<text class="badge" x="{x:.0f}" y="{y + 43}" text-anchor="middle">{badge}</text>'
            )
        elif track == "intro":
            labels = (
                f'<text class="name" x="{x:.0f}" y="{y - 20}" text-anchor="middle">{name}</text>'
                f'<text class="badge" x="{x:.0f}" y="{y + 26}" text-anchor="middle">{badge}</text>'
            )
        else:
            labels = (
                f'<text class="name" x="{x:.0f}" y="{y - 20}" text-anchor="middle">{name}</text>'
                f'<text class="badge" x="{x:.0f}" y="{y + 30}" text-anchor="middle">{badge}</text>'
            )
        current = ' aria-current="true"' if lab["slug"] == selected else ""
        stations.append(
            f'<a class="station track-{track}" href="#work-{e(lab["slug"])}" data-work="{e(lab["slug"])}"'
            f' aria-label="{e(lab["title"])}"{current}>'
            f'<circle cx="{x:.0f}" cy="{y}" r="{radius}"/>{labels}</a>'
        )
    return (
        '<svg class="route-map" viewBox="0 0 640 140" role="group" aria-label="Схема курса">'
        + "".join(lines)
        + "".join(stations)
        + "</svg>"
    )


def route_list(labs: list[dict], selected: str) -> str:
    """Та же схема для телефона: станции сверху вниз."""
    items = []
    for lab in labs:
        current = ' aria-current="true"' if lab["slug"] == selected else ""
        items.append(
            f'<li class="track-{lab.get("track", "main")}"><a href="#work-{e(lab["slug"])}" data-work="{e(lab["slug"])}"{current}>'
            f'<span class="dot"></span><span class="name">{e(station_name(lab))}</span>'
            f'<span class="badge">{e(lab.get("badge", ""))}</span></a></li>'
        )
    return f'<ol class="route-list">{"".join(items)}</ol>'


def work_panel(index: int, lab: dict, course: dict, files: dict[str, dict]) -> str:
    slug = lab["slug"]
    rows = [download_row("ipynb", "Ноутбук", files[f"labs:{slug}"])]
    if lab.get("lecture"):
        lecture = next(item for item in course["lectures"] if item["slug"] == lab["lecture"])
        title = "Лекция к работе" if lecture["title"].startswith("К ") else f"Лекция «{lecture['title']}»"
        rows.append(download_row("pdf", title, files[f"lectures:{lecture['slug']}"]))
    if lab.get("playground"):
        playground = next(item for item in course["playgrounds"] if item["url"] == lab["playground"])
        rows.append(file_row("web", f"Интерактивная страница «{playground['title']}»", playground["url"]))
    kwic = ""
    if lab.get("kwic"):
        left, key, right = lab["kwic"]
        # Пробелы нужны строке на телефоне; в сетке широкого экрана они не видны.
        before = "" if left.endswith(("(", "«")) else " "
        after = "" if right.startswith((",", ".", ":", ";", "!", "?", ")", "»")) else " "
        kwic = (
            f'<p class="kwic"><span class="kl">{e(left)}</span>{before}<mark>{e(key)}</mark>'
            f'{after}<span class="kr">{e(right)}</span></p>'
        )
    return (
        f'<section class="work-card" id="work-{e(slug)}" aria-labelledby="work-{e(slug)}-title">'
        f'<div class="work-head"><h2 id="work-{e(slug)}-title">{e(lab["title"])}</h2>'
        f'<span class="wid">{e(lab.get("badge", ""))}</span></div>'
        f"{kwic}"
        f'<p class="about">{inline(lab.get("about", ""))}</p>'
        f'<ul class="files">{"".join(rows)}</ul></section>'
    )


# Выбор работы на схеме. Стоит сразу после карточек, чтобы лишние скрылись
# до первой отрисовки; без JavaScript видны все карточки подряд.
SELECT_WORK = """<script>
(function () {
  var panels = document.querySelectorAll(".work-card");
  var stations = document.querySelectorAll(".route [data-work]");
  function select(slug, scroll) {
    var panel = document.getElementById("work-" + slug);
    if (!panel) return false;
    panels.forEach(function (item) { item.hidden = item !== panel; });
    stations.forEach(function (link) {
      if (link.getAttribute("data-work") === slug) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    });
    if (scroll && panel.getBoundingClientRect().top > window.innerHeight * 0.55) {
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    return true;
  }
  stations.forEach(function (link) {
    link.addEventListener("click", function (event) {
      event.preventDefault();
      var slug = link.getAttribute("data-work");
      select(slug, true);
      history.replaceState(null, "", "#work-" + slug);
    });
  });
  window.addEventListener("hashchange", function () {
    if (location.hash.indexOf("#work-") === 0) select(location.hash.slice(6), true);
  });
  var fromHash = location.hash.indexOf("#work-") === 0 && select(location.hash.slice(6), false);
  if (!fromHash) select(document.querySelector(".route").getAttribute("data-current"), false);
})();
</script>"""

# Ссылка на свёрнутый раздел (из текста или по адресу с #) раскрывает его.
OPEN_ON_LINK = """<script>
(function () {
  function open(hash) {
    var target = hash && document.getElementById(decodeURIComponent(hash.slice(1)));
    if (target && target.tagName === "DETAILS") target.open = true;
  }
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest('a[href^="#"]');
    if (link) open(link.hash);
  });
  window.addEventListener("hashchange", function () { open(location.hash); });
  open(location.hash);
})();
</script>"""


def section_announcements(course: dict) -> str:
    items = course.get("announcements") or []
    if not items:
        return ""
    lis = "\n".join(
        f'<li><span class="date">{e(item.get("date", ""))}</span>{inline(item["text"])}</li>' for item in items
    )
    return f'<ul class="announce">{lis}</ul>'


def section_about(course: dict) -> str:
    return paragraphs(course.get("about", ""))


def section_schedule(course: dict) -> str:
    rows = course.get("schedule") or []
    if not rows:
        return ""
    with_date = any(row.get("date") for row in rows)
    with_deadline = any(row.get("deadline") for row in rows)
    head = ["неделя", "тема", "работа"] + (["даты"] if with_date else []) + (["срок сдачи"] if with_deadline else [])
    body = []
    for row in rows:
        cells = [
            f'<td class="wk">{e(row.get("weeks", ""))}</td>',
            f'<td>{inline(row.get("topic", ""))}</td>',
            f'<td class="task">{e(row.get("work", ""))}</td>',
        ]
        if with_date:
            cells.append(f'<td class="when" data-label="даты">{e(row.get("date", ""))}</td>')
        if with_deadline:
            cells.append(f'<td class="when" data-label="срок">{e(row.get("deadline", ""))}</td>')
        body.append(f"<tr>{''.join(cells)}</tr>")
    thead = "".join(f"<th>{e(cell)}</th>" for cell in head)
    return f'<table class="schedule"><thead><tr>{thead}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def section_materials(course: dict, files: dict[str, dict]) -> str:
    kit = download_row(
        "zip",
        "Комплект для лабораторных",
        files["kit"],
        "Все ноутбуки, папка `data/` с корпусом и `requirements.txt`. Распакуйте и открывайте "
        "ноутбуки из этой папки — пути к данным в них относительные.",
    )
    labs = "".join(
        download_row("ipynb", lab["title"], files["labs:" + lab["slug"]]) for lab in course.get("labs", [])
    )
    lectures = "".join(
        download_row("pdf", item["title"], files["lectures:" + item["slug"]], item.get("about", ""))
        for item in course.get("lectures", [])
    )
    return (
        f'<ul class="files">{kit}</ul>'
        f'<h3>ноутбуки по отдельности</h3><ul class="files">{labs}</ul>'
        f'<h3>лекции</h3><ul class="files">{lectures}</ul>'
    )


def section_playgrounds(course: dict) -> str:
    items = course.get("playgrounds") or []
    if not items:
        return ""
    rows = "".join(
        file_row("web", item["title"], item["url"], desc=f"{item.get('about', '')} {item.get('work', '')}".strip())
        for item in items
    )
    return (
        '<p class="muted small">Те же числа на том же корпусе, что и в лабораторных, но параметры '
        "можно крутить. Работают в браузере, ставить ничего не нужно; первая загрузка — до минуты.</p>"
        f'<ul class="files">{rows}</ul>'
    )


def section_howto() -> str:
    return """<ol>
<li>Скачайте <a href="#materials">комплект для лабораторных</a> и распакуйте.
Открывайте ноутбуки из распакованной папки: пути к данным в них относительные.</li>
<li>Нужен Python 3.12 и библиотеки из <code>requirements.txt</code>:
<pre><code>pip install -r requirements.txt</code></pre>
Версии в файле проверены: на них все работы выполняются сверху вниз без
ошибок. TensorFlow нужен только Л.р. № 4 — если ставить его не хочется,
удалите строку <code>tensorflow</code> из <code>requirements.txt</code>.</li>
<li>Нужен интернет: вводная «Классификация» загружает наборы iris и penguins
через seaborn, а «Классификация текстов» в разделе 13 при первом запуске
скачивает корпус 20 Newsgroups (около 14 МБ).</li>
<li>Л.р. № 4 удобнее всего выполнять в
<a href="https://colab.research.google.com/">Google Colab</a>: «Файл →
Загрузить блокнот», затем «Среда выполнения → Сменить среду → GPU».</li>
</ol>"""


def section_literature(course: dict) -> str:
    items = course.get("literature") or []
    if not items:
        return ""
    lis = "\n".join(
        f"<li>{inline(item['text'])}"
        + (f' <a href="{e(item["url"])}">{e(item["url"])}</a>' if item.get("url") else "")
        + "</li>"
        for item in items
    )
    return f"<ul>{lis}</ul>"


CONTACT_LABELS = (
    ("teacher", "преподаватель"),
    ("email", "почта"),
    ("telegram", "Telegram"),
    ("office_hours", "консультации"),
)


def contact_value(key: str, value: str) -> str:
    if key == "email":
        return f'<a href="mailto:{e(value)}">{e(value)}</a>'
    if key == "telegram" and value.startswith("@"):
        return f'<a href="https://t.me/{e(value[1:])}">{e(value)}</a>'
    return inline(value)


def section_contacts(course: dict) -> str:
    contacts = course.get("contacts") or {}
    rows = [
        f"<dt>{label}</dt><dd>{contact_value(key, contacts[key])}</dd>"
        for key, label in CONTACT_LABELS
        if contacts.get(key)
    ]
    if not rows:
        return ""
    return f'<dl class="contacts">{"".join(rows)}</dl>'


def section_journal() -> str:
    return (
        "<p>Баллы за сданные работы. Журнал открывается по коду группы, "
        "который сообщает преподаватель.</p>"
        '<p><a class="tag-link" href="journal/">открыть журнал →</a></p>'
    )


def section_block(index: int, anchor: str, heading: str, body: str, hint: str, is_open: bool) -> str:
    hint_html = f'<span class="hint">{hint}</span>' if hint else ""
    return (
        f'<details class="section" id="{anchor}"{" open" if is_open else ""}>'
        f'<summary><span class="sid">s{index:02d}</span><h2>{e(heading)}</h2>{hint_html}</summary>'
        f'<div class="body">{body}</div></details>'
    )


SECTION_ANCHORS = (
    "news", "about", "schedule", "materials", "playgrounds", "howto", "literature", "contacts", "journal",
)


def render_index(course: dict, files: dict[str, dict]) -> str:
    opened = set(course.get("open_sections", ["materials"]))
    unknown = opened - set(SECTION_ANCHORS)
    if unknown:
        raise SystemExit(f"open_sections в course.toml: неизвестные разделы {sorted(unknown)}")
    check_route(course)
    labs = course["labs"]
    selected = selected_work(course)
    kit_size = files["kit"]["size"]
    sections = [
        ("news", "Объявления", section_announcements(course), ""),
        ("about", "О курсе", section_about(course), ""),
        ("schedule", "Расписание", section_schedule(course), ""),
        ("materials", "Все материалы", section_materials(course, files), f"комплект .zip · {e(kit_size)}"),
        ("playgrounds", "Интерактивные страницы", section_playgrounds(course), ""),
        ("howto", "Как запустить ноутбуки", section_howto(), ""),
        ("literature", "Литература", section_literature(course), ""),
        ("contacts", "Контакты", section_contacts(course), ""),
        ("journal", "Журнал группы", section_journal(), '<span class="chip chip-zip">по коду</span>'),
    ]
    assert tuple(anchor for anchor, *_ in sections) == SECTION_ANCHORS
    present = [section for section in sections if section[2]]
    blocks = "\n".join(
        section_block(index, anchor, heading, body, hint, anchor in opened)
        for index, (anchor, heading, body, hint) in enumerate(present, start=1)
    )
    panels = "\n".join(work_panel(index, lab, course, files) for index, lab in enumerate(labs))
    counts = " · ".join(
        (
            plural(len(labs), "работа", "работы", "работ"),
            plural(len(course.get("lectures", [])), "лекция", "лекции", "лекций"),
            plural(len(course.get("playgrounds", [])), "площадка", "площадки", "площадок"),
        )
    )
    subtitle = course.get("subtitle", "")
    if course.get("semester"):
        subtitle = f"{subtitle} · {course['semester']}" if subtitle else course["semester"]
    updated = dt.datetime.now(MOSCOW).strftime("%d.%m.%Y")
    title = course["title"]
    css = version(SITE / "style.css")
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{e(title)} — материалы курса</title>
<meta name="description" content="{e(title)}: лекции, лабораторные работы, расписание и журнал группы.">
<meta name="theme-color" content="#1d2430">
<link rel="preload" href="fonts/IBMPlexSans-Regular.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="fonts/IBMPlexMono-Regular.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="style.css?v={css}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='18' fill='%231d2430'/%3E%3Ccircle cx='30' cy='50' r='11' fill='none' stroke='%23c9d1dc' stroke-width='6'/%3E%3Ccircle cx='70' cy='50' r='11' fill='%23e58a7c'/%3E%3Cpath d='M41 50h18' stroke='%238fb3d9' stroke-width='8'/%3E%3C/svg%3E">
</head>
<body>
<div class="topbar"><div class="bar"><span><a href="{REPO_URL}">basic-linguistic-ml</a> <span class="sep">›</span> курс</span><span class="counts">{e(counts)}</span></div></div>
<div class="page">
<header class="site">
<p class="kicker">{e(subtitle)}</p>
<h1>{e(title)}</h1>
<p class="tags"><a class="tag-link" href="journal/">журнал группы →</a><a class="tag-link quiet" href="#materials">все материалы ↓</a></p>
</header>
<nav class="route" aria-label="Работы курса" data-current="{e(selected)}">
<p class="route-caption">маршрут курса · выберите работу</p>
{route_svg(labs, selected)}
{route_list(labs, selected)}
<p class="legend"><span class="solid"></span>конвейер локализации<span class="dashed"></span>вводная и демо</p>
</nav>
<div class="works">
{panels}
</div>
{SELECT_WORK}
<main class="sections">
{blocks}
</main>
<footer class="site">
обновлено {updated} · исходники и история изменений — <a href="{REPO_URL}">GitHub</a> ·
шрифты IBM Plex (<a href="fonts/OFL.txt">OFL</a>)
</footer>
</div>
{OPEN_ON_LINK}
</body>
</html>
"""


# ------------------------------------------------------------------ сборка


def load_course() -> dict:
    with open(SITE / "course.toml", "rb") as handle:
        return tomllib.load(handle)


def journal_works(course: dict) -> list[str]:
    """Названия столбцов журнала: поле journal у каждой работы, иначе title."""
    return [lab.get("journal") or lab["title"] for lab in course["labs"]]


def stamp_journal_page(page: Path, course: dict) -> None:
    text = page.read_text(encoding="utf-8")
    options = "".join(f'<option value="{e(name)}"></option>' for name in journal_works(course))
    for old, new in (
        ("<!-- course-works -->", options),
        ('href="../style.css"', f'href="../style.css?v={version(SITE / "style.css")}"'),
        ('src="journal.js"', f'src="journal.js?v={version(SITE / "journal" / "journal.js")}"'),
    ):
        if old not in text:
            raise SystemExit(f"в site/journal/index.html нет {old}")
        text = text.replace(old, new)
    page.write_text(text, encoding="utf-8")


def build(out: Path, journal: Path | None = None) -> None:
    course = load_course()
    check_labs(course["labs"])
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    files = copy_materials(course, out)
    (out / "index.html").write_text(render_index(course, files), encoding="utf-8")
    shutil.copyfile(SITE / "style.css", out / "style.css")
    shutil.copytree(SITE / "fonts", out / "fonts")
    shutil.copytree(SITE / "journal", out / "journal")
    stamp_journal_page(out / "journal" / "index.html", course)
    if journal is not None and journal.is_file() and journal.stat().st_size > 0:
        shutil.copyfile(journal, out / "journal" / "journal.json")
    # Сборка уже готовая: Jekyll на Pages ей не нужен.
    (out / ".nojekyll").write_text("", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="куда собрать сайт")
    parser.add_argument("--journal", type=Path, help="journal.json из ветки journal, если он есть")
    arguments = parser.parse_args()
    build(arguments.output.resolve(), arguments.journal)
    print(f"сайт собран в {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
