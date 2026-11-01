"""Собрать актуальные лекции XeLaTeX в PDF, не оставляя служебные файлы в курсе.

    python scripts/build_lectures.py
    python scripts/build_lectures.py --only mlg_intro_ml
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Лекции" / "tex"
DECKS = {
    "mlg_intro_ml": "МЛГ_Введение_в_машинное_обучение.pdf",
    "mlg_text_analysis": "МЛГ_Интеллектуальный_анализ_текстовых_данных.pdf",
    "mlg_text_as_data": "МЛГ_Текст_как_данные.pdf",
    "mlg_lab1_classification": "МЛГ_Л.р_№1_Классификация_текстов.pdf",
    "mlg_lab2_vectors_tm": "МЛГ_Л.р_№2_Векторные_представления_и_память_переводов.pdf",
    "mlg_lab3_mt_quality": "МЛГ_Л.р_№3_Машинный_перевод_и_оценка_качества.pdf",
    "mlg_lab4_generation": "МЛГ_Л.р_№4_Генерация_текста.pdf",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=DECKS)
    args = parser.parse_args()
    engine = shutil.which("xelatex")
    if not engine:
        parser.error("Не найден xelatex. Установите TeX Live или MiKTeX.")
    decks = {args.only: DECKS[args.only]} if args.only else DECKS
    for stem, target in decks.items():
        with tempfile.TemporaryDirectory(prefix=f"{stem}-") as temporary:
            command = [engine, "-interaction=nonstopmode", "-halt-on-error",
                       "-file-line-error", f"-output-directory={temporary}", f"{stem}.tex"]
            for _ in range(2):
                result = subprocess.run(command, cwd=SOURCE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)
                if result.returncode:
                    output = result.stdout.decode("utf-8", errors="replace")
                    print("\n".join(output.splitlines()[-70:]))
                    return result.returncode
            log = (Path(temporary) / f"{stem}.log").read_text(encoding="utf-8", errors="replace")
            warnings = [line for line in log.splitlines()
                        if "Overfull" in line or "Missing character:" in line]
            if warnings:
                print(f"{stem}: проблемы вёрстки:")
                print("\n".join(warnings))
                return 1
            shutil.copyfile(Path(temporary) / f"{stem}.pdf", ROOT / "Лекции" / target)
            print(f"Готово: {target}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
