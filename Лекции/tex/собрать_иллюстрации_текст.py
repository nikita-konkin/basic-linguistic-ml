# -*- coding: utf-8 -*-
"""Иллюстрации к лекции «Текст как данные» (МЛГ).

Запуск из Лекции/tex:

    python собрать_иллюстрации_текст.py ../../Л.р/data fig

Имена файлов латиницей (tad_*.pdf): XeLaTeX на Windows не открывает пути с
кириллицей. Вывод — PDF (вектор). Размер каждой фигуры равен её ширине на
слайде, поэтому шрифт 8 pt на рисунке и есть 8 pt на слайде.

Всё считается на учебном корпусе курса (Л.р/data/loc_corpus.csv, 160 сегментов)
теми же настройками, что в лабораторных:

* признаки — TF-IDF по символьным n-граммам 2–4 внутри слов (char_wb), как в
  Л.р. № 1 и на странице «данные и разметка»;
* разбиение — 120 обучающих и 40 отложенных сегментов, стратификация по типу,
  random_state=0, как у матрицы ошибок во «Введении»;
* BLEU и chrF — реализация Л.р. № 3 буква в букву (она же в
  gd_learn/mt_playground/metrics.py): нижний регистр, сглаживание +1 у BLEU,
  среднее F-мер по n = 1…6 у chrF.

Обучение классификатора показано градиентным спуском, написанным на NumPy:
sklearn прячет процесс, а эти слайды показывают именно его. Функция потерь —
перекрёстная энтропия без штрафа за веса, ровно та формула, что на слайде.
Сошедшийся спуск со штрафом sklearn (C = 1) даёт те же веса, что
LogisticRegression, — это проверяется здесь же и попадает в факты.

Числа, которые попадают на слайды, печатаются в конце и пишутся в
fig/tad_facts.json. Придуманных чисел на слайдах нет.
"""
import json
import math
import os
import re
import sys
from collections import Counter

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator
from scipy.stats import spearmanr
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

ДАННЫЕ, ВЫХОД = sys.argv[1], sys.argv[2]
os.makedirs(ВЫХОД, exist_ok=True)

СИНИЙ, КРАСНЫЙ, ЗЕЛЁНЫЙ, ФИОЛЕТ = "#2B4C6F", "#B03A2B", "#3E7D5A", "#6B5B95"
СЕРЫЙ, ЧЕРНИЛА, СВЕТЛЫЙ, ЛИНИЯ = "#8B9299", "#1B2433", "#ECEFF3", "#C9CFD5"
ТИПЫ = ["документация", "интерфейс", "маркетинг", "юридический"]  # порядок классов sklearn
ПОКАЗ = ["интерфейс", "документация", "маркетинг", "юридический"]  # порядок на слайдах
ЦВЕТ_ТИПА = {"интерфейс": СИНИЙ, "документация": ЗЕЛЁНЫЙ,
             "маркетинг": КРАСНЫЙ, "юридический": ФИОЛЕТ}
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8,
                     "axes.edgecolor": ЛИНИЯ, "axes.labelcolor": ЧЕРНИЛА,
                     "axes.labelsize": 8, "axes.titlesize": 8,
                     "text.color": ЧЕРНИЛА, "xtick.color": СЕРЫЙ,
                     "ytick.color": СЕРЫЙ, "xtick.labelsize": 7,
                     "ytick.labelsize": 7, "legend.fontsize": 7,
                     "legend.frameon": False, "figure.facecolor": "white",
                     "axes.spines.top": False, "axes.spines.right": False,
                     "pdf.fonttype": 42})
СМ = 1 / 2.54  # matplotlib меряет в дюймах, слайд — в сантиметрах
ШИРИНА = 13.8  # текстовое поле слайда 16:9 при полях 1,1 см

К = pd.read_csv(os.path.join(ДАННЫЕ, "loc_corpus.csv"))
ФАКТЫ = {}


def сохранить(fig, имя):
    п = os.path.join(ВЫХОД, имя)
    fig.savefig(п, bbox_inches="tight", pad_inches=0.03, facecolor="white")
    plt.close(fig)
    print("  ", имя, f"{os.path.getsize(п) / 1024:.0f} КБ")


def запятая(x, знаков=3):
    """Число так, как его печатают слайды: десятичная запятая."""
    return f"{x:.{знаков}f}".replace(".", ",")


ЗАПЯТАЯ = FuncFormatter(lambda x, _: f"{x:g}".replace(".", ",").replace("-", "−"))


def оси(ax, x=None, y=None, числа="xy"):
    """Подписи делений по-русски: запятая, и 1, 10, 100 вместо 10⁰, 10¹, 10².

    ``числа`` — у каких осей подписи числовые: у столбчатых диаграмм по оси y
    стоят n-граммы, и их трогать нельзя.
    """
    for ось, деления in (("x", x), ("y", y)):
        шкала = ax.xaxis if ось == "x" else ax.yaxis
        if деления is not None:
            шкала.set_major_locator(FixedLocator(деления))
            шкала.set_minor_locator(NullLocator())
        if ось in числа:
            шкала.set_major_formatter(ЗАПЯТАЯ)


def слова(текст):
    return re.findall(r"\w+", str(текст).lower())


def пробелы(ngram):
    """Пробел внутри символьной n-граммы виден только так."""
    return "«" + ngram.replace(" ", "␣") + "»"


# ======================================================================
# БЛОК 1. Текст как данные
# ======================================================================

def разреженность():
    """Корпус глазами модели. Вся матрица 160 × 5418 на слайде не видна
    (столбец уже пикселя), поэтому рисуется честный фрагмент, а доли — по всей."""
    v = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
    X = v.fit_transform(К["ru_ref"])
    имена = v.get_feature_names_out()
    df = np.asarray((X > 0).sum(axis=0)).ravel()
    ФАКТЫ["разреженность"] = {
        "признаков": int(X.shape[1]),
        "доля_ненулевых": round(float(X.nnz / (X.shape[0] * X.shape[1])), 4),
        "ненулевых_в_строке_в_среднем": round(float(X.nnz / X.shape[0]), 1),
        "ненулевых_в_строке_по_типам": {t: round(float(np.diff(X.indptr)[(К["type"] == t)
                                                                      .to_numpy()].mean()), 1)
                                        for t in ПОКАЗ},
        "n_грамм_в_одном_сегменте": int((df == 1).sum()),
    }
    # Фрагмент: по 4 первых сегмента каждого типа и 48 n-грамм, взятых
    # случайно (зерно 0) из всех 5418 и упорядоченных от частых к редким.
    строки = np.concatenate([np.where(К["type"] == t)[0][:4] for t in ПОКАЗ])
    rng = np.random.default_rng(0)
    столбцы = rng.choice(X.shape[1], size=48, replace=False)
    столбцы = столбцы[np.argsort(-df[столбцы], kind="stable")]
    фрагмент = (X[строки][:, столбцы] > 0).toarray()
    ФАКТЫ["разреженность"]["фрагмент"] = {
        "строк": int(len(строки)), "столбцов": int(len(столбцы)),
        "закрашено": int(фрагмент.sum()), "клеток": int(фрагмент.size)}

    fig, ax = plt.subplots(figsize=(7.2 * СМ, 3.9 * СМ))
    ax.set_xlim(-0.5, фрагмент.shape[1] - 0.5); ax.set_ylim(фрагмент.shape[0] - 0.5, -0.5)
    for i in range(фрагмент.shape[0]):
        цвет = ЦВЕТ_ТИПА[К["type"].iloc[строки[i]]]
        for j in range(фрагмент.shape[1]):
            ax.add_patch(plt.Rectangle((j - 0.42, i - 0.42), 0.84, 0.84, linewidth=0,
                                       color=цвет if фрагмент[i, j] else СВЕТЛЫЙ))
    ax.set_yticks([1.5, 5.5, 9.5, 13.5]); ax.set_yticklabels(ПОКАЗ, fontsize=6.5)
    ax.set_xticks([0, 47]); ax.set_xticklabels(["частые", "редкие"], fontsize=6.5)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xlabel(f"48 случайных n-грамм из {X.shape[1]}", fontsize=7, color=СЕРЫЙ)
    сохранить(fig, "tad_sparsity.pdf")


def ципф():
    """Ранг и частота слова на корпусе: в логарифмическом масштабе — почти прямая."""
    токены = [w for s in К["ru_ref"] for w in слова(s)]
    частоты = Counter(токены)
    упорядочено = частоты.most_common()
    ранги = np.arange(1, len(упорядочено) + 1)
    f = np.array([c for _, c in упорядочено])
    # Наклон — по словам, встретившимся больше одного раза: хвост из единиц —
    # горизонтальная полка, которая на маленьком корпусе исказила бы прямую.
    часть = f > 1
    наклон, сдвиг = np.polyfit(np.log(ранги[часть]), np.log(f[часть]), 1)
    hapax = int((f == 1).sum())
    ФАКТЫ["ципф"] = {
        "словоупотреблений": len(токены), "словоформ": len(частоты),
        "встречаются_один_раз": hapax,
        "доля_встречающихся_один_раз": round(hapax / len(частоты), 3),
        "наклон": round(float(наклон), 2),
        "первые_десять": [(w, c) for w, c in упорядочено[:10]],
        "однобуквенных_словоформ": int(sum(1 for w in частоты if len(w) == 1)),
    }

    fig, ax = plt.subplots(figsize=(7.4 * СМ, 4.3 * СМ))
    ax.loglog(ранги, f, "o", ms=2.2, color=СИНИЙ, alpha=0.7, mec="none")
    xs = np.array([1, ранги[часть].max()])
    ax.loglog(xs, np.exp(сдвиг) * xs ** наклон, color=КРАСНЫЙ, lw=1)
    for r, (dx, dy) in zip(range(4), [(6, 0), (6, 2), (6, -1), (6, -4)]):
        w, c = упорядочено[r]
        ax.annotate(f"«{w}» — {c}", (r + 1, c), xytext=(dx, dy), textcoords="offset points",
                    fontsize=6.8, color=ЧЕРНИЛА, va="center")
    ax.axhspan(0.85, 1.18, color=СВЕТЛЫЙ, zorder=0)
    # Подпись — внутри серой полосы слева: там точек нет, единицы начинаются правее.
    ax.text(1.15, 1.0, f"{hapax} из {len(частоты)} — по одному разу",
            fontsize=6.5, color=СЕРЫЙ, ha="left", va="center")
    ax.set_xlabel("ранг слова (1 — самое частое)")
    ax.set_ylabel("частота")
    ax.set_ylim(0.8, 70)
    оси(ax, x=[1, 10, 100, 689], y=[1, 2, 5, 10, 20, 35])
    сохранить(fig, "tad_zipf.pdf")


def tf_idf():
    """Разбор TF-IDF одного сегмента по формуле и IDF как функция df."""
    v = TfidfVectorizer()  # настройки Л.р. № 1 для слов
    X = v.fit_transform(К["ru_ref"])
    словарь = v.vocabulary_
    N = len(К)
    df = np.asarray((X > 0).sum(axis=0)).ravel()
    idf = v.idf_
    i = К.index[К["id"] == "s044"][0]
    строка = X[i].toarray().ravel()
    счёт = Counter(w for w in слова(К["ru_ref"][i]) if len(w) > 1)
    разбор = []
    for w in dict.fromkeys(w for w in слова(К["ru_ref"][i]) if len(w) > 1):
        j = словарь[w]
        разбор.append({"слово": w, "tf": счёт[w], "df": int(df[j]),
                       "idf": round(float(idf[j]), 3),
                       "tf_x_idf": round(float(счёт[w] * idf[j]), 3),
                       "вес": round(float(строка[j]), 3)})
    длина = math.sqrt(sum((d["tf"] * idf[словарь[d["слово"]]]) ** 2 for d in разбор))
    # Проверка, что формула на слайде — это ровно то, что делает sklearn.
    for d in разбор:
        assert abs(d["tf"] * idf[словарь[d["слово"]]] / длина - строка[словарь[d["слово"]]]) < 1e-9
        assert abs(math.log((1 + N) / (1 + d["df"])) + 1 - d["idf"]) < 1e-3
    ФАКТЫ["tf_idf"] = {"сегмент": К["ru_ref"][i], "N": N, "признаков_слов": len(словарь),
                       "длина_вектора_до_нормировки": round(длина, 3), "разбор": разбор}

    fig, (лев, прав) = plt.subplots(1, 2, figsize=(ШИРИНА * СМ, 3.1 * СМ),
                                    gridspec_kw={"width_ratios": [1, 1.1], "wspace": 0.5})
    сетка = np.linspace(1, 40, 200)
    лев.plot(сетка, np.log((1 + N) / (1 + сетка)) + 1, color=СИНИЙ, lw=1.2)
    for w, (dx, dy) in [("значение", (6, 2)), ("умолчанию", (6, 2)), ("если", (6, -4)),
                        ("по", (5, 4)), ("не", (5, 4))]:
        j = словарь[w]
        лев.plot(df[j], idf[j], "o", color=КРАСНЫЙ, ms=3.2)
        лев.annotate(f"«{w}»", (df[j], idf[j]), xytext=(dx, dy), textcoords="offset points",
                     fontsize=6.8)
    лев.set_xlabel("в скольких сегментах есть слово")
    лев.set_ylabel("IDF")
    лев.set_ylim(1, 5.9)
    оси(лев, x=[1, 10, 20, 30, 40], y=[1, 2, 3, 4, 5])

    подписи = [d["слово"] for d in разбор]
    y = np.arange(len(подписи))[::-1]
    tf_норм = np.array([d["tf"] for d in разбор], float)
    tf_норм = tf_норм / np.linalg.norm(tf_норм)
    прав.barh(y + 0.19, tf_норм, height=0.36, color=ЛИНИЯ, label="только частота")
    прав.barh(y - 0.19, [d["вес"] for d in разбор], height=0.36, color=СИНИЙ,
              label="TF-IDF")
    прав.set_yticks(y); прав.set_yticklabels(подписи, fontsize=7)
    прав.tick_params(axis="y", length=0)
    прав.set_xlabel("вес слова в векторе сегмента")
    прав.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, handlelength=1)
    оси(прав, x=[0, 0.2, 0.4, 0.6], числа="x")
    сохранить(fig, "tad_idf.pdf")


# ======================================================================
# БЛОК 2. Математический минимум: обучение классификатора по шагам
# ======================================================================

def косинус_вручную():
    """Две пары для ручного счёта: словами и символьными n-граммами."""
    def кос(a, b):
        return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
    a, b = "Не удалось подключиться к серверу", "Не удалось подключиться к базе данных"
    v = CountVectorizer(token_pattern=r"(?u)\b\w+\b").fit([a, b])
    A, B = v.transform([a]).toarray().ravel(), v.transform([b]).toarray().ravel()
    c, d = "Настройки поиска", "Поиск по настройкам"
    vw = CountVectorizer(token_pattern=r"(?u)\b\w+\b").fit([c, d])
    vc = CountVectorizer(analyzer="char_wb", ngram_range=(2, 4)).fit([c, d])
    C, D = (vc.transform([t]).toarray().ravel() for t in (c, d))
    ФАКТЫ["косинус"] = {
        "пара_1": [a, b], "словарь_1": list(v.get_feature_names_out()),
        "A": A.tolist(), "B": B.tolist(), "скалярное": int(A @ B),
        "длины_в_квадрате": [int(A @ A), int(B @ B)], "косинус_1": round(кос(A, B), 3),
        "пара_2": [c, d],
        "косинус_2_слова": round(кос(*[vw.transform([t]).toarray().ravel() for t in (c, d)]), 3),
        "косинус_2_символы_2_4": round(кос(C, D), 3),
        "символьных_2_4_общих": int(((C > 0) & (D > 0)).sum()),
        "символьных_2_4_всего": [int((C > 0).sum()), int((D > 0).sum())],
    }


def softmax(Z):
    Z = Z - Z.max(axis=1, keepdims=True)
    E = np.exp(Z)
    return E / E.sum(axis=1, keepdims=True)


class Спуск:
    """Мультиномиальная логистическая регрессия, обученная полным градиентным спуском.

    Модель:   z_k = w_k · x + b_k,   p_k = exp(z_k) / Σ_j exp(z_j)
    Потери:   L = −(1/N) Σ_i ln p_{y_i}(x_i)   (+ λ/2 · ‖W‖², если λ > 0)
    Градиент: ∂L/∂w_k = (1/N) Σ_i (p_ik − [y_i = k]) · x_i   (+ λ · w_k)
    Шаг:      w ← w − η · ∂L/∂w
    """

    def __init__(self, Xtr, ytr, Xte, yte, шаг, шагов, следить=(), λ=0.0, снимки=False):
        n, f = Xtr.shape
        Y = np.eye(4)[ytr]
        W, b = np.zeros((f, 4)), np.zeros(4)
        история, вер = [], []
        self.веса = {к: [] for к in следить}  # (признак, класс) -> траектория
        self.снимки = {}
        for t in range(шагов + 1):
            Ptr, Pte = softmax(Xtr @ W + b), softmax(Xte @ W + b)
            история.append((
                t,
                -np.mean(np.log(np.clip(Ptr[np.arange(n), ytr], 1e-300, 1))),
                -np.mean(np.log(np.clip(Pte[np.arange(len(yte)), yte], 1e-300, 1))),
                float((Ptr.argmax(1) == ytr).mean()),
                float((Pte.argmax(1) == yte).mean()),
            ))
            for (j, k) in следить:
                self.веса[(j, k)].append(W[j, k])
            вер.append(Pte)
            if снимки:
                self.снимки[t] = (W.copy(), b.copy())
            G = (Ptr - Y) / n
            W -= шаг * (Xtr.T @ G + λ * W)
            b -= шаг * G.sum(axis=0)
        self.W, self.b = W, b
        self.история = np.array(история)
        self.вероятности_контроль = np.array(вер)


ВЕРНЫЙ = "Компания оставляет за собой право в любое время вносить изменения в настоящие Условия."
НЕВЕРНЫЙ = "Согласие может быть отозвано в любое время."


def обучение():
    Xtr_t, Xte_t, ytr_t, yte_t = train_test_split(
        К["ru_ref"], К["type"], test_size=0.25, stratify=К["type"], random_state=0)
    v = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)).fit(Xtr_t)
    Xtr, Xte = v.transform(Xtr_t).toarray(), v.transform(Xte_t).toarray()
    ytr = np.array([ТИПЫ.index(t) for t in ytr_t])
    yte = np.array([ТИПЫ.index(t) for t in yte_t])
    имена = v.get_feature_names_out()
    номер = {g: j for j, g in enumerate(имена)}
    тексты = list(Xte_t)

    ШАГ, ШАГОВ = 8.0, 3000
    следим = [(номер["ить "], ТИПЫ.index("интерфейс")),
              (номер["ются"], ТИПЫ.index("документация")),
              (номер["сто"], ТИПЫ.index("юридический")),
              (номер["ить "], ТИПЫ.index("юридический"))]
    с = Спуск(Xtr, ytr, Xte, yte, ШАГ, ШАГОВ, следить=следим)
    h = с.история

    # Проверка: со штрафом sklearn (C = 1, то есть λ = 1/N) спуск приходит
    # к тем же весам, что LogisticRegression.
    n = len(ytr)
    sk = LogisticRegression(max_iter=5000).fit(Xtr, ytr)
    со_штрафом = Спуск(Xtr, ytr, Xte, yte, ШАГ, ШАГОВ, λ=1 / n)
    расхождение = float(np.abs(со_штрафом.W - sk.coef_.T).max())
    assert расхождение < 0.01, расхождение
    взрыв = Спуск(Xtr, ytr, Xte, yte, 30.0, ШАГОВ, λ=1 / n).история

    def точка(t):
        return {"шаг": int(t), "потери_обуч": round(float(h[t, 1]), 3),
                "потери_контроль": round(float(h[t, 2]), 3),
                "точность_обуч": round(float(h[t, 3]), 3),
                "точность_контроль": round(float(h[t, 4]), 3)}
    итог = с.вероятности_контроль[-1]
    ФАКТЫ["обучение"] = {
        "обучающих": n, "отложенных": int(len(yte)),
        "признаков": int(Xtr.shape[1]), "шаг_eta": ШАГ, "шагов": ШАГОВ,
        "ln4": round(math.log(4), 3),
        "точки": [точка(t) for t in (0, 1, 2, 3, 5, 10, 30, 100, 300, 1000, 3000)],
        "лучшая_точность_контроль": round(float(h[:, 4].max()), 3),
        "шаги_лучшей": [int(t) for t in np.where(h[:, 4] == h[:, 4].max())[0]],
        "ошибок_на_отложенных": int((итог.argmax(1) != yte).sum()),
        "ошибки": [(тексты[i], ТИПЫ[yte[i]], ТИПЫ[int(итог[i].argmax())])
                   for i in range(len(yte)) if итог[i].argmax() != yte[i]],
        "со_штрафом_sklearn": {
            "макс_расхождение_весов_с_sklearn": round(расхождение, 4),
            "точность_контроль": round(float(со_штрафом.история[-1, 4]), 3),
            "sklearn_точность": round(float(sk.score(Xte, yte)), 3),
            "потери_обуч_в_конце": round(float(со_штрафом.история[-1, 1]), 3),
            "eta_30_потери_через_3000": round(float(взрыв[-1, 1]), 2),
            "eta_30_точность_контроль": round(float(взрыв[-1, 4]), 3),
        },
    }

    # --- кривые обучения ------------------------------------------------
    t = h[1:, 0]
    fig, (лев, прав) = plt.subplots(1, 2, figsize=(ШИРИНА * СМ, 3.7 * СМ),
                                    gridspec_kw={"wspace": 0.32})
    лев.semilogx(t, h[1:, 1], color=СИНИЙ, lw=1.4, label="обучающие (120)")
    лев.semilogx(t, h[1:, 2], color=КРАСНЫЙ, lw=1.4, label="отложенные (40)")
    лев.axhline(math.log(4), color=СЕРЫЙ, ls="--", lw=0.8)
    лев.text(1.1, math.log(4) + 0.04, "до обучения: ln 4 ≈ 1,386", fontsize=6.5,
             color=СЕРЫЙ, va="bottom")
    лев.set_ylim(0, 1.6)
    лев.set_xlabel("шаг градиентного спуска"); лев.set_ylabel("потери L")
    # Подписи прямо у кривых: справа синяя у нуля, красная выше 0,6, и легенде
    # там не уместиться, не задев одну из них.
    лев.text(ШАГОВ, 0.14, "обучающие", color=СИНИЙ, fontsize=6.8, va="bottom", ha="right")
    лев.text(ШАГОВ, 0.54, "отложенные", color=КРАСНЫЙ, fontsize=6.8, va="top", ha="right")
    прав.semilogx(t, h[1:, 3], color=СИНИЙ, lw=1.4, label="на обучающих")
    прав.semilogx(t, h[1:, 4], color=КРАСНЫЙ, lw=1.4, label="на отложенных")
    прав.axhline(0.25, color=СЕРЫЙ, ls="--", lw=0.8)
    прав.text(1.1, 0.27, "ответ наугад: 0,25", fontsize=6.5, color=СЕРЫЙ, va="bottom")
    прав.set_ylim(0, 1.05)
    прав.set_xlabel("шаг градиентного спуска"); прав.set_ylabel("доля верных")
    прав.legend(loc="center right", bbox_to_anchor=(1.0, 0.42))
    for ax, деления in ((лев, [0, 0.5, 1, 1.5]), (прав, [0, 0.25, 0.5, 0.75, 1])):
        ax.set_xlim(1, ШАГОВ)
        оси(ax, x=[1, 10, 100, 1000], y=деления)
    сохранить(fig, "tad_training.pdf")

    # --- траектории отдельных весов -------------------------------------
    подписи = {следим[0]: ("«ить␣» → интерфейс", СИНИЙ, "-"),
               следим[1]: ("«ются» → документация", ЗЕЛЁНЫЙ, "-"),
               следим[2]: ("«сто» → юридический", ФИОЛЕТ, "-"),
               следим[3]: ("«ить␣» → юридический", ФИОЛЕТ, "--")}
    fig, ax = plt.subplots(figsize=(5.0 * СМ, 4.0 * СМ))
    концы = {к: float(с.веса[к][-1]) for к in подписи}
    высота, выше = {}, None
    for к in sorted(концы, key=концы.get, reverse=True):
        высота[к] = концы[к] if выше is None else min(концы[к], выше - 0.32)
        выше = высота[к]
    for к, (подпись, цвет, стиль) in подписи.items():
        путь = np.array(с.веса[к])[1:]
        ax.semilogx(t, путь, color=цвет, ls=стиль, lw=1.3)
        ax.text(ШАГОВ * 1.15, высота[к], подпись, color=цвет, fontsize=6.6, va="center",
                clip_on=False)
    ax.axhline(0, color=ЛИНИЯ, lw=0.6)
    ax.set_xlim(1, ШАГОВ)
    ax.set_xlabel("шаг градиентного спуска"); ax.set_ylabel("вес n-граммы")
    оси(ax, x=[1, 10, 100, 1000], y=[-1, 0, 1, 2, 3])
    ФАКТЫ["обучение"]["веса"] = {подписи[к][0]: {"шаг_1": round(float(с.веса[к][1]), 3),
                                                 "шаг_10": round(float(с.веса[к][10]), 2),
                                                 "шаг_3000": round(float(с.веса[к][-1]), 2)}
                                  for к in следим}
    сохранить(fig, "tad_weights_path.pdf")

    # --- что модель выучила ---------------------------------------------
    fig, axes = plt.subplots(1, 4, figsize=(ШИРИНА * СМ, 3.6 * СМ),
                             gridspec_kw={"wspace": 0.8})
    ФАКТЫ["обучение"]["топ_весов"] = {}
    for ax, тип in zip(axes, ПОКАЗ):
        k = ТИПЫ.index(тип)
        лучшие = np.argsort(-с.W[:, k])[:6]
        y = np.arange(6)[::-1]
        ax.barh(y, с.W[лучшие, k], color=ЦВЕТ_ТИПА[тип], height=0.65)
        ax.set_yticks(y)
        ax.set_yticklabels([пробелы(имена[j]) for j in лучшие], fontsize=7.5,
                           family="DejaVu Sans Mono")
        ax.tick_params(axis="y", length=0)
        ax.set_title(тип, color=ЦВЕТ_ТИПА[тип], fontweight="bold")
        ax.set_xlim(0, 3.6)
        оси(ax, x=[0, 1, 2, 3], числа="x")
        ФАКТЫ["обучение"]["топ_весов"][тип] = [(пробелы(имена[j]), round(float(с.W[j, k]), 2))
                                              for j in лучшие]
    axes[0].set_xlabel("вес", labelpad=1)
    сохранить(fig, "tad_top_ngrams.pdf")

    # --- softmax и вклад n-грамм на двух отложенных сегментах ----------------
    разборы = {}
    fig, axes = plt.subplots(1, 2, figsize=(ШИРИНА * СМ, 2.75 * СМ),
                             gridspec_kw={"wspace": 0.55})
    for ax, текст in zip(axes, (ВЕРНЫЙ, НЕВЕРНЫЙ)):
        i = тексты.index(текст)
        x = Xte[i]
        z = x @ с.W + с.b
        p = softmax(z[None, :])[0]
        k = int(p.argmax())
        вклад = x * с.W[:, k]
        лучшие = [j for j in np.argsort(-вклад) if вклад[j] > 0][:7]
        y = np.arange(len(лучшие))[::-1]
        ax.barh(y, вклад[лучшие], color=ЦВЕТ_ТИПА[ТИПЫ[k]], height=0.65)
        ax.set_yticks(y)
        ax.set_yticklabels([пробелы(имена[j]) for j in лучшие], fontsize=7.2,
                           family="DejaVu Sans Mono")
        ax.tick_params(axis="y", length=0)
        ax.set_xlabel(f"вклад в балл «{ТИПЫ[k]}»")
        ax.set_title(("верно: " if k == yte[i] else "ошибка: ") + f"{ТИПЫ[k]}, "
                     f"p = {запятая(p[k])}", fontsize=7.5,
                     color=ЗЕЛЁНЫЙ if k == yte[i] else КРАСНЫЙ)
        оси(ax, x=[0, 0.1, 0.2], числа="x")
        разборы[текст] = {
            "верный": ТИПЫ[yte[i]], "ответ": ТИПЫ[k],
            "z": {ТИПЫ[c]: round(float(z[c]), 2) for c in range(4)},
            "exp_z": {ТИПЫ[c]: round(float(np.exp(z[c])), 2) for c in range(4)},
            "p": {ТИПЫ[c]: round(float(p[c]), 3) for c in range(4)},
            "минус_ln_p_верного": round(float(-np.log(p[yte[i]])), 3),
            "n_грамм_в_сегменте": int((x > 0).sum()),
            "вклад_лучших": [(пробелы(имена[j]), round(float(вклад[j]), 3)) for j in лучшие],
            "сумма_вкладов_все": round(float(вклад.sum()), 2), "b": round(float(с.b[k]), 2),
            "вероятности_на_шаге_10": {ТИПЫ[c]: round(float(с.вероятности_контроль[10, i, c]), 3)
                                       for c in range(4)},
        }
    ФАКТЫ["обучение"]["разборы"] = разборы
    сохранить(fig, "tad_contributions.pdf")

    # --- как растёт уверенность на тех же двух сегментах --------------------
    fig, axes = plt.subplots(1, 2, figsize=(ШИРИНА * СМ, 2.9 * СМ),
                             gridspec_kw={"wspace": 0.12}, sharey=True)
    for ax, текст in zip(axes, (ВЕРНЫЙ, НЕВЕРНЫЙ)):
        i = тексты.index(текст)
        for тип in ПОКАЗ:
            k = ТИПЫ.index(тип)
            ax.semilogx(t, с.вероятности_контроль[1:, i, k], color=ЦВЕТ_ТИПА[тип],
                        lw=2.2 if k == yte[i] else 1.0)
        ax.set_xlim(1, ШАГОВ); ax.set_ylim(0, 1)
        ax.set_title("«" + " ".join(текст.split()[:4]) + "…»", fontsize=6.8, color=СЕРЫЙ)
        ax.set_xlabel("шаг градиентного спуска")
        оси(ax, x=[1, 10, 100, 1000], y=[0, 0.25, 0.5, 0.75, 1])
    axes[0].set_ylabel("вероятность класса")
    from matplotlib.lines import Line2D
    ручки = [Line2D([], [], color=ЦВЕТ_ТИПА[тип], lw=1.0) for тип in ПОКАЗ]
    ручки.append(Line2D([], [], color=СЕРЫЙ, lw=2.2))
    fig.legend(ручки, list(ПОКАЗ) + ["толстая — верный класс"], loc="lower center",
               bbox_to_anchor=(0.5, 0.98), ncol=5, fontsize=6.3, handlelength=1.4,
               columnspacing=1.0, handletextpad=0.4)
    сохранить(fig, "tad_confidence.pdf")

    # --- размер шага -------------------------------------------------------
    fig, ax = plt.subplots(figsize=(7.4 * СМ, 4.5 * СМ))
    размер = {}
    for η, цвет, подпись in [(0.5, СЕРЫЙ, "η = 0,5"), (8.0, СИНИЙ, "η = 8"),
                             (30.0, КРАСНЫЙ, "η = 30")]:
        hh = Спуск(Xtr, ytr, Xte, yte, η, 60).история
        ax.plot(hh[:, 0], hh[:, 1], color=цвет, lw=1.3, marker="o", ms=1.5, label=подпись)
        размер[подпись] = {"шаг_2": round(float(hh[2, 1]), 2),
                           "максимум": round(float(hh[:, 1].max()), 2),
                           "шаг_максимума": int(hh[:, 1].argmax()),
                           "шаг_20": round(float(hh[20, 1]), 2),
                           "шаг_60": round(float(hh[60, 1]), 2)}
    ax.set_yscale("log")
    ax.set_ylim(0.007, 25)
    ax.set_xlim(0, 60)
    ax.set_xlabel("шаг градиентного спуска"); ax.set_ylabel("потери на обучающих")
    оси(ax, x=[0, 10, 20, 30, 40, 50, 60], y=[0.01, 0.1, 1, 10])
    ax.legend(loc="upper right")
    ФАКТЫ["размер_шага"] = размер
    сохранить(fig, "tad_learning_rate.pdf")

    # --- обучение, которое можно нарисовать: два признака -------------------
    svd = TruncatedSVD(2, random_state=0).fit(Xtr)
    Ztr, Zte = svd.transform(Xtr), svd.transform(Xte)
    μ, σ = Ztr.mean(axis=0), Ztr.std(axis=0)
    Ztr, Zte = (Ztr - μ) / σ, (Zte - μ) / σ
    д = Спуск(Ztr, ytr, Zte, yte, 1.0, 300, снимки=True)
    кадры = [1, 3, 10, 300]
    x0, x1 = Ztr[:, 0].min() - 0.3, Ztr[:, 0].max() + 0.3
    y0, y1 = Ztr[:, 1].min() - 0.3, Ztr[:, 1].max() + 0.3
    xx, yy = np.meshgrid(np.linspace(x0, x1, 300), np.linspace(y0, y1, 300))
    сетка = np.c_[xx.ravel(), yy.ravel()]
    палитра = ListedColormap([ЦВЕТ_ТИПА[тип] for тип in ТИПЫ])
    fig, axes = plt.subplots(1, 4, figsize=(ШИРИНА * СМ, 3.0 * СМ),
                             gridspec_kw={"wspace": 0.06})
    for ax, кадр in zip(axes, кадры):
        W, b = д.снимки[кадр]
        класс = softmax(сетка @ W + b).argmax(1).reshape(xx.shape)
        ax.contourf(xx, yy, класс, levels=[-0.5, 0.5, 1.5, 2.5, 3.5], cmap=палитра, alpha=0.17)
        ax.contour(xx, yy, класс, levels=[0.5, 1.5, 2.5], colors="white", linewidths=0.9)
        ax.scatter(Ztr[:, 0], Ztr[:, 1], c=[ЦВЕТ_ТИПА[ТИПЫ[k]] for k in ytr], s=4,
                   linewidths=0)
        ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(True); s.set_color(ЛИНИЯ)
        ax.set_title(f"шаг {кадр}: потери {запятая(д.история[кадр, 1], 2)}\n"
                     f"точность {запятая(д.история[кадр, 3], 2)}", fontsize=6.6, linespacing=1.25)
    ФАКТЫ["два_признака"] = {
        "точность_обуч": round(float(д.история[-1, 3]), 3),
        "точность_контроль": round(float(д.история[-1, 4]), 3),
        "по_кадрам": {кадр: [round(float(д.история[кадр, 1]), 3),
                             round(float(д.история[кадр, 3]), 3)] for кадр in кадры},
    }
    # Легенда цветов типов — одной строкой под кадрами: в маленький кадр она не влезает.
    for k, тип in enumerate(ПОКАЗ):
        fig.text(0.13 + 0.22 * k, -0.02, "●", color=ЦВЕТ_ТИПА[тип], fontsize=8, va="top")
        fig.text(0.152 + 0.22 * k, -0.025, тип, fontsize=7, va="top")
    сохранить(fig, "tad_boundary.pdf")


# ======================================================================
# БЛОК 4. Метрики: реализация Л.р. № 3
# ======================================================================
ТОКЕН = re.compile(r"\w+", re.UNICODE)


def _токены(текст):
    return ТОКЕН.findall(str(текст).lower())


def _ngrams(посл, n):
    return Counter(tuple(посл[i:i + n]) for i in range(len(посл) - n + 1))


def bleu(гипотеза, эталон, max_n=4, сглаживание=1.0, подробно=False):
    hyp, ref = _токены(гипотеза), _токены(эталон)
    if not hyp or not ref:
        return 0.0
    доли, разбор = [], []
    for n in range(1, max_n + 1):
        hn, rn = _ngrams(hyp, n), _ngrams(ref, n)
        всего = sum(hn.values())
        if всего == 0:
            continue
        совпало = sum(min(c, rn[g]) for g, c in hn.items())
        доли.append((совпало + сглаживание) / (всего + сглаживание))
        разбор.append({"n": n, "совпало": совпало, "всего": всего,
                       "p_n": round(доли[-1], 3)})
    среднее = math.exp(sum(math.log(p) for p in доли) / len(доли))
    штраф = 1.0 if len(hyp) > len(ref) else math.exp(1 - len(ref) / max(len(hyp), 1))
    if подробно:
        return {"c": len(hyp), "r": len(ref), "доли": разбор,
                "среднее_геом": round(среднее, 3), "BP": round(штраф, 3),
                "BLEU": round(среднее * штраф, 3)}
    return float(среднее * штраф)


def chrf(гипотеза, эталон, max_n=6, beta=2.0, подробно=False):
    hyp, ref = " ".join(_токены(гипотеза)), " ".join(_токены(эталон))
    if not hyp or not ref:
        return 0.0
    f, разбор = [], []
    for n in range(1, max_n + 1):
        hn, rn = _ngrams(hyp, n), _ngrams(ref, n)
        if not hn or not rn:
            continue
        совпало = sum(min(c, rn[g]) for g, c in hn.items())
        P, R = совпало / sum(hn.values()), совпало / sum(rn.values())
        f.append(0.0 if P + R == 0 else (1 + beta ** 2) * P * R / (beta ** 2 * P + R))
        разбор.append({"n": n, "совпало": совпало, "в_гипотезе": sum(hn.values()),
                       "в_эталоне": sum(rn.values()), "P": round(P, 3), "R": round(R, 3),
                       "F": round(f[-1], 3)})
    if подробно:
        return {"разбор": разбор, "chrF": round(sum(f) / len(f), 3)}
    return float(sum(f) / len(f))


def метрики():
    ФАКТЫ["bleu_разбор"] = {
        "отрицание": bleu("Удалось подключиться к серверу",
                          "Не удалось подключиться к серверу", подробно=True),
        "формы": bleu("Сохранение изменений", "Сохранить изменения", подробно=True),
    }
    ФАКТЫ["chrf_разбор"] = {
        "формы": chrf("Сохранение изменений", "Сохранить изменения", подробно=True),
        "отрицание": chrf("Удалось подключиться к серверу",
                          "Не удалось подключиться к серверу", подробно=True),
    }
    b = np.array([bleu(h, r) for h, r in zip(К["ru_mt"], К["ru_ref"])])
    c = np.array([chrf(h, r) for h, r in zip(К["ru_mt"], К["ru_ref"])])
    ρ = spearmanr(b, c).statistic
    ФАКТЫ["bleu_chrf"] = {"медиана_bleu": round(float(np.median(b)), 3),
                          "медиана_chrf": round(float(np.median(c)), 3),
                          "спирмен": round(float(ρ), 2),
                          "отмечены": {}}

    fig, ax = plt.subplots(figsize=(7.2 * СМ, 4.1 * СМ))
    for тип in ПОКАЗ:
        m = (К["type"] == тип).to_numpy()
        ax.scatter(b[m], c[m], s=7, color=ЦВЕТ_ТИПА[тип], alpha=0.75, linewidths=0, label=тип)
    ax.axvline(np.median(b), color=СЕРЫЙ, ls="--", lw=0.7)
    ax.axhline(np.median(c), color=СЕРЫЙ, ls="--", lw=0.7)
    # Место подписи — в координатах графика, там, где точек нет.
    for sid, подпись, (tx, ty), ha in [
            ("s002", "s002: сломан\nплейсхолдер", (0.70, 0.40), "left"),
            ("s065", "s065: верный\nперевод", (0.0, 0.90), "left"),
            ("s115", "s115: смысл\nпотерян", (0.205, 0.10), "left"),
            ("s012", "s012: сломан\nплейсхолдер", (0.45, 0.24), "left")]:
        i = К.index[К["id"] == sid][0]
        ax.scatter([b[i]], [c[i]], s=28, facecolors="none", edgecolors=ЧЕРНИЛА, linewidths=0.9)
        ax.annotate(подпись, (b[i], c[i]), xytext=(tx, ty), textcoords="data",
                    fontsize=6.3, color=ЧЕРНИЛА, ha=ha, va="center", linespacing=1.1,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85),
                    arrowprops=dict(arrowstyle="-", color=ЧЕРНИЛА, lw=0.5,
                                    shrinkA=0, shrinkB=3.5))
        ФАКТЫ["bleu_chrf"]["отмечены"][sid] = [round(float(b[i]), 3), round(float(c[i]), 3)]
    ax.set_xlim(-0.02, 1.04); ax.set_ylim(-0.02, 1.04)
    ax.set_xlabel("BLEU"); ax.set_ylabel("chrF")
    оси(ax, x=[0, 0.2, 0.4, 0.6, 0.8, 1], y=[0, 0.2, 0.4, 0.6, 0.8, 1])
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=6.5,
              handletextpad=0.2)
    ax.text(np.median(b) + 0.01, 1.02, "медиана BLEU", fontsize=6.3, color=СЕРЫЙ, va="top")
    ax.text(1.02, np.median(c) - 0.02, "медиана chrF", fontsize=6.3, color=СЕРЫЙ,
            ha="right", va="top")
    сохранить(fig, "tad_bleu_chrf.pdf")


print("иллюстрации к «Тексту как данным» (МЛГ):")
разреженность(); ципф(); tf_idf(); косинус_вручную(); обучение(); метрики()
print("\nФАКТЫ ДЛЯ СЛАЙДОВ:")
print(json.dumps(ФАКТЫ, ensure_ascii=False, indent=1, default=str))
with open(os.path.join(ВЫХОД, "tad_facts.json"), "w", encoding="utf-8") as f:
    json.dump(ФАКТЫ, f, ensure_ascii=False, indent=1, default=str)
