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


def inline(text: str) -> str:
    """Экранировать текст, `код` оформить как код, ссылки сделать ссылками."""
    out = e(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return URL.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', out)


def paragraphs(text: str) -> str:
    blocks = [" ".join(block.split()) for block in text.strip().split("\n\n") if block.strip()]
    return "\n".join(f"<p>{inline(block)}</p>" for block in blocks)


def download_link(info: dict, label: str = "скачать") -> str:
    return (
        f'<a class="download" href="{e(info["href"])}" download="{e(info["download"])}">'
        f"{e(label)}</a><span class=\"meta\">{e(info['size'])}</span>"
    )


def section_announcements(course: dict) -> str:
    items = course.get("announcements") or []
    if not items:
        return ""
    lis = "\n".join(
        f'<li><span class="date">{e(item.get("date", ""))}</span>{inline(item["text"])}</li>'
        for item in items
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
    head = ["неделя", "тема", "работа"]
    if with_date:
        head.append("даты")
    if with_deadline:
        head.append("срок сдачи")
    body = []
    for row in rows:
        cells = [e(row.get("weeks", "")), inline(row.get("topic", "")), e(row.get("work", ""))]
        if with_date:
            cells.append(e(row.get("date", "")))
        if with_deadline:
            cells.append(e(row.get("deadline", "")))
        body.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    thead = "".join(f"<th>{e(cell)}</th>" for cell in head)
    return (
        f'<div class="table-wrap"><table><thead><tr>{thead}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def section_materials(course: dict, files: dict[str, dict]) -> str:
    kit = files["kit"]
    kit_card = (
        '<ul class="cards"><li class="kit">'
        '<div class="title">Комплект для лабораторных</div>'
        '<p class="about">Все ноутбуки, папка <code>data/</code> с корпусом и '
        "<code>requirements.txt</code> в одном архиве. Распакуйте и открывайте "
        "ноутбуки из этой папки — пути к данным в них относительные.</p>"
        f"{download_link(kit, 'скачать архив')}</li></ul>"
    )
    labs = "\n".join(
        f'<li><div class="title">{e(lab["title"])}</div>'
        f'<p class="about">{inline(lab.get("about", ""))}</p>'
        f'{download_link(files["labs:" + lab["slug"]], "ноутбук .ipynb")}</li>'
        for lab in course.get("labs", [])
    )
    lectures = "\n".join(
        f'<li><div class="title">{e(item["title"])}</div>'
        f'<p class="about">{inline(item.get("about", ""))}</p>'
        f'{download_link(files["lectures:" + item["slug"]], "PDF")}</li>'
        for item in course.get("lectures", [])
    )
    return (
        f"<h3>Лабораторные работы</h3>{kit_card}"
        '<p class="muted small" style="margin-top:0.6rem">По отдельности:</p>'
        f'<ul class="cards">{labs}</ul>'
        f'<h3>Лекции</h3><ul class="cards">{lectures}</ul>'
    )


def section_playgrounds(course: dict) -> str:
    items = course.get("playgrounds") or []
    if not items:
        return ""
    lis = "\n".join(
        f'<li><div class="title"><a href="{e(item["url"])}">{e(item["title"])}</a></div>'
        f'<p class="about">{inline(item.get("about", ""))}</p>'
        f'<div class="meta">{e(item.get("work", ""))}</div></li>'
        for item in items
    )
    return (
        "<p>Те же числа на том же корпусе, что и в лабораторных, но параметры "
        "можно крутить. Работают прямо в браузере, ставить ничего не нужно; "
        "первая загрузка занимает до минуты.</p>"
        f'<ul class="cards">{lis}</ul>'
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
        f'<li>{inline(item["text"])}'
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
        '<p><a class="download" href="journal/">Открыть журнал</a></p>'
    )


# Ссылка на свёрнутый раздел (из меню или по адресу с #) раскрывает его.
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


def section_block(anchor: str, heading: str, body: str, is_open: bool) -> str:
    return (
        f'<details class="section" id="{anchor}"{" open" if is_open else ""}>'
        f"<summary><h2>{e(heading)}</h2></summary>"
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
    # (якорь, пункт меню, заголовок раздела, содержимое)
    sections = [
        ("news", "Объявления", "Объявления", section_announcements(course)),
        ("about", "О курсе", "О курсе", section_about(course)),
        ("schedule", "Расписание", "Расписание", section_schedule(course)),
        ("materials", "Материалы", "Материалы", section_materials(course, files)),
        ("playgrounds", "Интерактивные страницы", "Интерактивные страницы", section_playgrounds(course)),
        ("howto", "Как запустить", "Как запустить ноутбуки", section_howto()),
        ("literature", "Литература", "Литература", section_literature(course)),
        ("contacts", "Контакты", "Контакты", section_contacts(course)),
        ("journal", "Журнал", "Журнал группы", section_journal()),
    ]
    assert tuple(anchor for anchor, *_ in sections) == SECTION_ANCHORS
    present = [section for section in sections if section[3]]
    nav = " ".join(
        f'<a href="#{anchor}" class="journal-link">{e(title)}</a>'
        if anchor == "journal"
        else f'<a href="#{anchor}">{e(title)}</a>'
        for anchor, title, _, _ in present
    )
    blocks = "\n".join(
        section_block(anchor, heading, body, anchor in opened) for anchor, _, heading, body in present
    )
    subtitle = course.get("subtitle", "")
    if course.get("semester"):
        subtitle = f"{subtitle} · {course['semester']}" if subtitle else course["semester"]
    updated = dt.datetime.now(MOSCOW).strftime("%d.%m.%Y")
    title = course["title"]
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} — материалы курса</title>
<meta name="description" content="{e(title)}: лекции, лабораторные работы, расписание и журнал группы.">
<link rel="stylesheet" href="style.css?v={version(SITE / 'style.css')}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='18' fill='%232b4c6f'/%3E%3Ctext x='50' y='68' font-size='52' text-anchor='middle' fill='white' font-family='sans-serif'%3EML%3C/text%3E%3C/svg%3E">
</head>
<body>
<div class="page">
<header class="site">
<p class="kicker">Материалы курса</p>
<h1>{e(title)}</h1>
<p class="subtitle">{e(subtitle)}</p>
</header>
<nav class="toc" aria-label="Разделы">{nav}</nav>
<main>
{blocks}
</main>
<footer class="site">
Обновлено {updated}. Исходники материалов и история изменений —
<a href="{REPO_URL}">на GitHub</a>.
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
