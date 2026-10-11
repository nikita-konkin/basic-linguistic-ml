"""Проверки сборки сайта курса (scripts/build_site.py).

    python -m pytest tests/test_site.py -q

Сайт собирается во временную папку; проверяется, что все материалы на
месте, ссылки с главной ведут на существующие файлы и что на сайт не
попало то, что студентам выдавать нельзя.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_site  # noqa: E402
from run_notebooks import LEGACY_MARKERS  # noqa: E402

# Ни в путях, ни в содержимом сайта этого быть не должно: методические
# материалы преподавателя и ключ к Л.р. № 3.
FORBIDDEN = ("Критерии_оценивания", "План_правки", "Шпаргалка", "Ключ_разметки")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs))


@pytest.fixture(scope="module")
def course() -> dict:
    return build_site.load_course()


@pytest.fixture(scope="module")
def dist(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("site") / "dist"
    build_site.build(out)
    return out


@pytest.fixture(scope="module")
def index(dist) -> str:
    return (dist / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def links(index) -> list[dict[str, str | None]]:
    parser = Links()
    parser.feed(index)
    return parser.links


def test_every_lecture_and_notebook_is_published(course, dist):
    for item in course["lectures"]:
        path = dist / "files" / "lectures" / f"{item['slug']}.pdf"
        assert path.read_bytes() == build_site.resolve(item["file"]).read_bytes(), item["file"]
    for item in course["labs"]:
        path = dist / "files" / "labs" / f"{item['slug']}.ipynb"
        assert json.loads(path.read_text(encoding="utf-8"))["cells"], item["file"]


def test_lab_list_matches_the_folder(course):
    # Та же проверка, что делает сборка: новая работа не потеряется молча.
    build_site.check_labs(course["labs"])
    assert len(course["labs"]) == 5


def test_local_links_resolve(dist, links):
    local = [link["href"] for link in links if link.get("href") and not re.match(r"[a-z]+:|#", link["href"])]
    assert local, "на главной нет ни одной ссылки на файлы"
    for href in local:
        target = dist / href
        if href.endswith("/"):
            target = target / "index.html"
        assert target.is_file(), href


def test_downloads_keep_russian_names(course, links):
    names = {link["download"] for link in links if link.get("download")}
    for item in course["lectures"] + course["labs"]:
        assert nfc(Path(item["file"]).name) in names, item["file"]
    assert build_site.KIT_NAME in names


def test_kit_has_notebooks_data_and_requirements(course, dist):
    with zipfile.ZipFile(dist / "files" / build_site.KIT_SLUG) as archive:
        names = archive.namelist()
    folder = build_site.KIT_FOLDER
    notebooks = [name for name in names if name.endswith(".ipynb")]
    assert sorted(notebooks) == sorted(f"{folder}/{nfc(Path(lab['file']).name)}" for lab in course["labs"])
    assert f"{folder}/data/loc_corpus.csv" in names
    assert f"{folder}/requirements.txt" in names
    assert all(name == nfc(name) for name in names), "имена в архиве должны быть в NFC"
    assert not [name for name in names if any(marker in name for marker in LEGACY_MARKERS)]


def test_kit_is_reproducible(course, tmp_path):
    first, second = tmp_path / "a.zip", tmp_path / "b.zip"
    build_site.build_kit(course, first)
    build_site.build_kit(course, second)
    assert first.read_bytes() == second.read_bytes()


def test_nothing_private_is_published(dist):
    for path in dist.rglob("*"):
        relative = nfc(str(path.relative_to(dist)))
        assert not any(word in relative for word in FORBIDDEN), relative
        if path.suffix in {".html", ".js", ".css", ".txt"}:
            text = path.read_text(encoding="utf-8")
            assert not any(word in text for word in FORBIDDEN), relative
    with zipfile.ZipFile(dist / "files" / build_site.KIT_SLUG) as archive:
        assert not any(word in nfc(name) for name in archive.namelist() for word in FORBIDDEN)


def test_no_grading_page_yet(dist, links):
    # Страница оценивания отложена (BACKLOG.md): ни страницы, ни ссылок.
    assert not (dist / "grading").exists()
    assert not any("grading" in (link.get("href") or "") for link in links)


def test_journal_page_is_published(dist, links):
    page = (dist / "journal" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'src="journal\.js\?v=[0-9a-f]{10}"', page)
    assert re.search(r'href="\.\./style\.css\?v=[0-9a-f]{10}"', page)
    assert 'name="robots" content="noindex"' in page
    assert (dist / "journal" / "journal.js").is_file()
    assert any(link.get("href") == "journal/" for link in links)
    # Без --journal файла журнала в сборке нет: страница предложит его создать.
    assert not (dist / "journal" / "journal.json").exists()


def test_journal_file_is_copied_when_given(tmp_path):
    journal = tmp_path / "journal.json"
    journal.write_text('{"format": 1}\n', encoding="utf-8")
    out = tmp_path / "dist"
    build_site.build(out, journal)
    assert (out / "journal" / "journal.json").read_text(encoding="utf-8") == '{"format": 1}\n'

    # Пустой файл (ветки journal ещё нет) не публикуется.
    journal.write_text("", encoding="utf-8")
    build_site.build(out, journal)
    assert not (out / "journal" / "journal.json").exists()


def test_index_escapes_course_text(course):
    course = dict(course, about="<script>alert(1)</script> и `код`")
    files = {key: {"href": "x", "download": "x", "size": "1 КБ"} for key in ("kit",)}
    files.update({f"labs:{lab['slug']}": files["kit"] for lab in course["labs"]})
    files.update({f"lectures:{item['slug']}": files["kit"] for item in course["lectures"]})
    page = build_site.render_index(course, files)
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
    assert "<code>код</code>" in page


def test_sections_fold_and_only_chosen_ones_are_open(course, index):
    sections = dict(re.findall(r'<details class="section" id="(\w+)"( open)?>', index))
    assert {"about", "schedule", "materials", "howto", "journal"} <= set(sections)
    opened = {anchor for anchor, flag in sections.items() if flag}
    assert opened == set(course.get("open_sections", ["materials"])) & set(sections)
    # Ссылки меню ведут на существующие разделы.
    for anchor in re.findall(r'<nav class="toc"[^>]*>(.*?)</nav>', index, re.S)[0].split('href="#')[1:]:
        assert anchor.split('"')[0] in sections


def test_unknown_open_section_is_an_error(course):
    with pytest.raises(SystemExit):
        build_site.render_index(dict(course, open_sections=["matrials"]), {})


def test_journal_suggests_course_works(course, dist):
    page = (dist / "journal" / "index.html").read_text(encoding="utf-8")
    options = re.findall(r'<option value="([^"]*)">', page)
    assert options == build_site.journal_works(course)
    assert len(options) == len(course["labs"]) == len(set(options))


def test_empty_sections_are_hidden(course, index):
    if not course.get("announcements"):
        assert 'id="news"' not in index
    if not any((course.get("contacts") or {}).values()):
        assert 'id="contacts"' not in index
