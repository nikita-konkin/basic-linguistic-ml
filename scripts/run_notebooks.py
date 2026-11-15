"""Прогнать лабораторные ноутбуки сверху вниз и упасть, если что-то сломалось.

Обёртка над `pytest --nbmake`. Нужна по двум причинам.

Первая: имена файлов нельзя писать литералом. `Л_р_№3_Машинный_перевод...`
лежит на диске в нормализации NFD, остальные — в NFC. На macOS разница
незаметна, на Linux это **разные имена**, и захардкоженный путь там не найдётся.
Скрипт берёт имена из `os.listdir`, то есть ровно те байты, что лежат на диске.

Вторая: работы № 4 и № 5 требуют отдельных нейросетевых окружений.
По умолчанию они пропускаются; соответствующие флаги включают их явно.

    python scripts/run_notebooks.py              # работы 1-3
    python scripts/run_notebooks.py --with-tensorflow
    python scripts/run_notebooks.py --with-translation
    python scripts/run_notebooks.py --with-translation --only 5
    python scripts/run_notebooks.py --list       # что вообще нашлось
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB_DIR = ROOT / "Л.р"

# Ноутбуки предыдущей версии курса: оставлены в папке, но не поддерживаются.
# Вводная Л_р_№1_Классификация.ipynb (Scikit-Learn на ирисах) в курсе остаётся
# и прогоняется вместе с остальными.
LEGACY_MARKERS = ("4_задание", "L_w_")
TENSORFLOW_MARKER = "4_Генерация"
TRANSLATION_MARKER = "5_Адаптация"


def find_notebooks(
    lab_dir: Path = LAB_DIR,
    with_tensorflow: bool = False,
    with_translation: bool = False,
) -> list[str]:
    """Имена текущих ноутбуков, прочитанные с диска как есть."""
    names = sorted(name for name in os.listdir(lab_dir) if name.endswith(".ipynb"))
    current = [name for name in names if not any(marker in name for marker in LEGACY_MARKERS)]
    if not with_tensorflow:
        current = [name for name in current if TENSORFLOW_MARKER not in name]
    if not with_translation:
        current = [name for name in current if TRANSLATION_MARKER not in name]
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-tensorflow", action="store_true", help="включить работу № 4")
    parser.add_argument("--with-translation", action="store_true", help="включить работу № 5")
    parser.add_argument("--only", choices=("1", "2", "3", "4", "5"), help="выполнить только указанный номер")
    parser.add_argument("--list", action="store_true", help="показать список и выйти")
    parser.add_argument("--timeout", type=int, default=900, help="предел на ячейку, секунд")
    arguments = parser.parse_args()

    notebooks = find_notebooks(
        with_tensorflow=arguments.with_tensorflow,
        with_translation=arguments.with_translation,
    )
    if arguments.only:
        notebooks = [name for name in notebooks if name.startswith(f"Л_р_№{arguments.only}_")]
    if not notebooks:
        print("Ноутбуки не найдены", file=sys.stderr)
        return 1

    for name in notebooks:
        print(f"  {name}")
    if arguments.list:
        return 0

    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--nbmake",
            f"--nbmake-timeout={arguments.timeout}",
            "-p",
            "no:cacheprovider",
            "-v",
            *notebooks,
        ],
        cwd=LAB_DIR,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
