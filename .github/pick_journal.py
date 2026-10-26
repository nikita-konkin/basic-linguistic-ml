"""Сделать загруженный файл журнала файлом journal.json.

    python .github/pick_journal.py <коммит до пуша>

Сайт берёт из этой ветки только journal.json. Если журнал загружен через
GitHub (Add file → Upload files) под другим именем — `journal-3.json`,
`journal (1).json`, как его называет браузер при повторном скачивании, —
файл проверяется и переименовывается в journal.json. Печатает, что сделано;
ненулевой код выхода — если загружено что-то непонятное, тогда сайт не
пересобирается и journal.json остаётся прежним.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

TARGET = "journal.json"
UPLOADED = re.compile(r"^journal.*\.json$", re.IGNORECASE)
ZERO = "0" * 40


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def changed_files(before: str) -> list[str]:
    if not before or before == ZERO:
        return git("ls-files").split("\n")
    return git("diff", "--name-only", "--no-renames", before, "HEAD").split("\n")


def check(path: Path) -> None:
    """Похоже ли на журнал со страницы журнала: формат 1, два ящика."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["format"] == 1 and data["kdf"]["iterations"] > 0
        for box in ("view", "teacher"):
            assert all(isinstance(data[box][key], str) and data[box][key] for key in ("salt", "iv", "data"))
    except (ValueError, KeyError, TypeError, AssertionError):
        sys.exit(f"{path.name}: это не файл журнала (ожидался journal.json со страницы журнала)")


def main() -> None:
    before = sys.argv[1] if len(sys.argv) > 1 else ""
    uploaded = sorted(
        name
        for name in changed_files(before)
        if UPLOADED.match(name) and name != TARGET and Path(name).is_file()
    )
    if not uploaded:
        check(Path(TARGET))
        print(f"{TARGET} обновлён, переименовывать нечего")
        return
    if len(uploaded) > 1:
        sys.exit(f"загружено сразу несколько журналов: {', '.join(uploaded)} — оставьте один")
    name = uploaded[0]
    check(Path(name))
    git("mv", "-f", name, TARGET)
    print(f"{name} → {TARGET}")


if __name__ == "__main__":
    main()
