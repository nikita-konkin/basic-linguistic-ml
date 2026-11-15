"""Проверки методики Л.р. № 5 без загрузки модели и без PyTorch.

    python -m pytest tests/test_translation_lab.py -q

Выполняются определения из самого ноутбука, чтобы проверять студенческий код.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import StratifiedGroupKFold

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = next((ROOT / "Л.р").glob("Л_р_№5_*.ipynb"))


@pytest.fixture(scope="module")
def lab():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    namespace = {
        "pd": pd, "np": np, "re": re, "json": json, "hashlib": hashlib,
        "unicodedata": unicodedata, "SequenceMatcher": SequenceMatcher,
        "Counter": Counter, "StratifiedGroupKFold": StratifiedGroupKFold,
        "TfidfVectorizer": TfidfVectorizer, "cosine_similarity": cosine_similarity,
    }
    tags = {"lab5-data", "lab5-split", "lab5-tm", "lab5-qa", "lab5-model"}
    constants = {"FIELD", "NUMBER", "NEG_EN", "NEG_RU"}
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        # Синтаксис проверяется и у тяжёлых ячеек, но обучение здесь не запускается.
        tree = ast.parse(source)
        if cell["id"] not in tags:
            continue
        definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                       or isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets)]
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(NOTEBOOK), "exec"), namespace)
    return namespace


@pytest.fixture(scope="module")
def corpus(lab):
    return lab["load_corpus"](ROOT / "Л.р" / "data" / "loc_corpus.csv")


def test_near_duplicates_stay_together(lab, corpus):
    duplicates = corpus.iloc[:2].copy()
    duplicates["id"] = ["duplicate-a", "duplicate-b"]
    data = pd.concat([corpus, duplicates], ignore_index=True)
    split = lab["split_corpus"](data)
    assert split.groupby("group")["split"].nunique().eq(1).all()
    assert split.groupby("split")["type"].nunique().eq(4).all()
    for original, added in zip(corpus.iloc[:2]["id"], duplicates["id"]):
        rows = split[split["id"].isin([original, added])]
        assert rows["group"].nunique() == rows["split"].nunique() == 1


def test_template_variants_form_one_group(lab):
    data = pd.DataFrame({"id": ["a", "b", "c"], "en": [
        "Delete file {name} after 12 hours", "DELETE file {path} after 24 hours!",
        "Contact technical support",
    ]})
    groups = lab["duplicate_groups"](data)
    assert groups[0] == groups[1] != groups[2]


def test_split_covers_technical_cases_without_using_references(lab, corpus):
    split = lab["split_corpus"](corpus)
    for _, part in split.groupby("split"):
        assert part["en"].str.contains(r"\{[^}]*\}|%[sd]|<[^>]+>", regex=True).any()
        assert part["en"].str.contains(r"\d", regex=True).any()
    changed = corpus.copy()
    changed["ru_ref"] = "Тестовый ответ, который не должен влиять на разбиение"
    changed_split = lab["split_corpus"](changed)
    pd.testing.assert_frame_equal(split[["id", "group", "split"]], changed_split[["id", "group", "split"]])


def test_tm_uses_only_supplied_memory_and_no_test_reference(lab):
    memory = pd.DataFrame({"id": ["train1", "train2"], "en": ["Save changes", "Delete file"],
                           "ru_ref": ["Сохранить изменения", "Удалить файл"]})
    queries = pd.DataFrame({"id": ["test1"], "en": ["Save changes now"]})
    result = lab["translation_memory"](memory, queries)
    assert result.iloc[0]["tm_id"] == "train1"
    assert result.iloc[0]["prediction"] == "Сохранить изменения"
    # Искомый русский перевод не нужен функции и не может попасть в словарь.
    assert "ru_ref" not in queries


def test_zero_vector_returns_no_tm_suggestion(lab):
    memory = pd.DataFrame({"id": ["a"], "en": ["aaaa"], "ru_ref": ["ответ"]})
    query = pd.DataFrame({"id": ["b"], "en": ["zzzz"]})
    result = lab["translation_memory"](memory, query).iloc[0]
    assert result["prediction"] == result["tm_id"] == ""
    assert result["tm_cosine"] == 0


def test_alignment_rejects_missing_and_duplicate_predictions(lab, corpus):
    reference = corpus.iloc[:2]
    predictions = pd.DataFrame([{"id": i, "system": system, "prediction": "x"}
                                for i in reference["id"] for system in ["tm", "base", "adapted"]])
    shuffled = predictions.sample(frac=1, random_state=7)
    aligned = lab["aligned_predictions"](shuffled, reference)
    assert (aligned["en"] == aligned["id"].map(reference.set_index("id")["en"])).all()
    with pytest.raises(ValueError, match="идентификаторы"):
        lab["aligned_predictions"](predictions.iloc[:-1], reference)
    with pytest.raises(ValueError, match="повторные"):
        lab["aligned_predictions"](pd.concat([predictions, predictions.iloc[:1]]), reference)


def test_checks_detect_repeated_field_loss_and_changed_number(lab):
    flags = lab["formal_checks"]("Copy {name} to {name}, 12 files", "Скопировать {name}, 13 файлов")
    assert "поля/теги" in flags and "числа" in flags
    assert "числа" not in lab["formal_checks"]("Value: 1.5", "Значение: 1,5")
    assert "возможная потеря отрицания" not in lab["formal_checks"]("Cannot connect", "Подключение невозможно")


def test_fingerprint_ignores_order_but_detects_changed_reference(lab, corpus):
    fingerprint = lab["corpus_fingerprint"]
    assert fingerprint(corpus) == fingerprint(corpus.iloc[::-1])
    changed = corpus.copy()
    changed.loc[0, "ru_ref"] += "!"
    assert fingerprint(changed) != fingerprint(corpus)


def test_saved_run_rejects_other_data_or_settings(lab, corpus):
    directory = ROOT / "Л.р" / "data" / "lab5_demo"
    experiment = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["experiment"]
    split = lab["split_corpus"](corpus)
    manifest, predictions, history = lab["load_saved_run"](directory, corpus, split, experiment)
    assert manifest["best_epoch"] == int(history.loc[history.validation_loss.idxmin(), "epoch"])
    assert set(predictions["id"]) == set(split.query("split == 'test'")["id"])
    with pytest.raises(ValueError, match="Параметры"):
        lab["load_saved_run"](directory, corpus, split, dict(experiment, epochs=99))
    with pytest.raises(ValueError, match="другому корпусу"):
        lab["load_saved_run"](directory, corpus.iloc[1:], split, experiment)


def test_heavy_notebooks_require_separate_flags():
    spec = importlib.util.spec_from_file_location("lab5_runner", ROOT / "scripts" / "run_notebooks.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert NOTEBOOK.name not in runner.find_notebooks()
    assert NOTEBOOK.name in runner.find_notebooks(with_translation=True)
    assert not any(runner.TENSORFLOW_MARKER in name for name in runner.find_notebooks(with_translation=True))
    assert len(runner.find_notebooks(with_tensorflow=True, with_translation=True)) == 6
