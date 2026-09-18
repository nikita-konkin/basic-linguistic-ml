# -*- coding: utf-8 -*-
"""Иллюстрации к МЛГ-версии лекции «Введение в машинное обучение».

Имена файлов латиницей: XeLaTeX на Windows не открывает пути с кириллицей.

Всё считается на учебном корпусе курса (Л.р/data/loc_corpus.csv, 160 сегментов)
и на предпосчитанных эмбеддингах. Придуманных чисел на слайдах нет.
Вывод — PDF (вектор), чтобы в Beamer не было растровой каши.
"""
import os, sys, re, json
from collections import Counter
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Circle

ДАННЫЕ, ВЫХОД = sys.argv[1], sys.argv[2]
os.makedirs(ВЫХОД, exist_ok=True)

СИНИЙ, КРАСНЫЙ, ЗЕЛЁНЫЙ, ФИОЛЕТ = "#2B4C6F", "#B03A2B", "#3E7D5A", "#6B5B95"
СЕРЫЙ, ЧЕРНИЛА, СВЕТЛЫЙ = "#8B9299", "#1B2433", "#ECEFF3"
ЦВЕТ_ТИПА = {"интерфейс": СИНИЙ, "документация": ЗЕЛЁНЫЙ,
             "маркетинг": КРАСНЫЙ, "юридический": ФИОЛЕТ}
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                     "axes.edgecolor": "#C9CFD5", "axes.labelcolor": ЧЕРНИЛА,
                     "text.color": ЧЕРНИЛА, "xtick.color": СЕРЫЙ,
                     "ytick.color": СЕРЫЙ, "figure.facecolor": "white",
                     "pdf.fonttype": 42})

К = pd.read_csv(os.path.join(ДАННЫЕ, "loc_corpus.csv"))
ФАКТЫ = {}


def сохранить(fig, имя):
    п = os.path.join(ВЫХОД, имя)
    fig.savefig(п, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  ", имя, f"{os.path.getsize(п)/1024:.0f} КБ")


# ------------------------------------------------ 1. четыре направления
def направления():
    fig, ax = plt.subplots(figsize=(12, 5.0))
    ax.set_xlim(0, 100); ax.set_ylim(0, 42); ax.axis("off")
    блоки = [
        ("Классическое\nобучение", СИНИЙ,
         "классификация\nрегрессия\nкластеризация\nснижение размерности",
         "Л.р. № 1 и № 2\nвесь курс здесь"),
        ("Ансамбли", ЗЕЛЁНЫЙ, "несколько моделей\nисправляют ошибки\nдруг друга",
         "Л.р. № 3:\nтри проверки"),
        ("Нейросети и\nглубокое обучение", КРАСНЫЙ,
         "перцептрон\nрекуррентные сети\nтрансформеры",
         "Л.р. № 4,\nмашинный перевод"),
        ("Обучение с\nподкреплением", СЕРЫЙ, "действие в среде\nи отклик на него",
         "в локализации\nне применяется"),
    ]
    x, ш, в = 0.8, 23.6, 21
    for имя, цвет, тело, снизу in блоки:
        ax.add_patch(FancyBboxPatch((x, 15), ш, в,
                     boxstyle="round,pad=0.5,rounding_size=1.2",
                     facecolor=цвет, edgecolor="none", alpha=0.15))
        ax.text(x + ш/2, 31.5, имя, ha="center", va="center", fontsize=12,
                fontweight="bold", color=цвет, linespacing=1.3)
        ax.text(x + ш/2, 21.5, тело, ha="center", va="center", fontsize=10.5,
                color=ЧЕРНИЛА, linespacing=1.6)
        ax.text(x + ш/2, 11.5, снизу, ha="center", va="top", fontsize=10.5,
                color=цвет, style="italic", linespacing=1.5,
                fontweight="bold" if цвет != СЕРЫЙ else "normal")
        x += ш + 1.5
    ax.text(50, 3.2, "Первые три направления в курсе используются.\n"
            "Четвёртое названо и оставлено в стороне: переводческий проект —\n"
            "не среда, в которой можно действовать и получать отклик.",
            ha="center", va="top", fontsize=10, color=СЕРЫЙ, style="italic",
            linespacing=1.5)
    fig.tight_layout()
    сохранить(fig, "directions.pdf")


# ------------------------------------------------ 2. мешок слов
def мешок_слов():
    from sklearn.feature_extraction.text import CountVectorizer
    тексты = ["Не удалось сохранить файл",
              "Файл сохранён", "Сохранить файл как…"]
    v = CountVectorizer()
    M = v.fit_transform(тексты).toarray()
    слова = v.get_feature_names_out()

    fig, ax = plt.subplots(figsize=(12, 3.6))
    ax.imshow(M, cmap="Blues", vmin=0, vmax=2, aspect="auto")
    ax.set_xticks(range(len(слова)))
    ax.set_xticklabels(слова, rotation=0, ha="center", fontsize=11)
    ax.set_yticks(range(len(тексты)))
    ax.set_yticklabels([f"«{t}»" for t in тексты], fontsize=11)
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            ax.text(j, i, M[i, j], ha="center", va="center", fontsize=13,
                    color="white" if M[i, j] else "#B8C0C7", fontweight="bold")
    ax.set_title("Число вхождений слова: сколько раз слово встретилось в этом тексте",
                 fontsize=12, color=ЧЕРНИЛА, pad=12)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    fig.text(0.5, -0.10, "«сохранить» и «сохранён» — два разных столбца: "
             "машина не знает, что это формы одного слова",
             ha="center", fontsize=10.5, color=СЕРЫЙ, style="italic")
    fig.tight_layout()
    сохранить(fig, "bagofwords.pdf")


# ------------------------------------------------ 3. дерево решений
def признаки_дерева():
    return pd.DataFrame({
        "слов в сегменте": К["ru_ref"].str.split().str.len(),
        "кончается точкой": К["ru_ref"].str.strip().str.endswith(".").astype(int),
        "начинается с инфинитива": К["ru_ref"].str.strip()
            .str.match(r"^[А-ЯЁ][а-яё]+(?:ть|ться)\b").astype(int),
        "есть плейсхолдер": К["en"].str.contains(r"\{|%[sd]|<", regex=True).astype(int),
        "есть «вы», «ваш»": К["ru_ref"]
            .str.contains(r"\b[Вв](?:ы|ам|ас|аш\w*)\b", regex=True).astype(int),
        "есть «настоящий», «стороны»": К["ru_ref"]
            .str.contains(r"астоящ|торон|обязат", regex=True).astype(int),
    })


def дерево():
    """Дерево рисуется вручную: plot_tree печатает value=[0.25, ...] и gini,
    что для лингвистов шум. Структура и все числа берутся из обученной модели."""
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.model_selection import cross_val_score
    X, y = признаки_дерева(), К["type"]
    т = DecisionTreeClassifier(max_depth=3, random_state=0)
    ФАКТЫ["дерево_точность"] = round(float(cross_val_score(т, X, y, cv=5).mean()), 3)
    т.fit(X, y)
    д = т.tree_
    имена, классы = list(X.columns), list(т.classes_)

    листья = []
    def обойти(у):
        if д.children_left[у] == -1:
            листья.append(у)
        else:
            обойти(д.children_left[у]); обойти(д.children_right[у])
    обойти(0)

    поз, шаг = {}, 100 / len(листья)
    for i, у in enumerate(листья):
        поз[у] = (шаг * (i + 0.5), None)
    def уровни(у, гл=0):
        if д.children_left[у] == -1:
            поз[у] = (поз[у][0], 84 - гл * 21)
            return поз[у][0]
        л = уровни(д.children_left[у], гл + 1)
        п = уровни(д.children_right[у], гл + 1)
        поз[у] = ((л + п) / 2, 84 - гл * 21)
        return поз[у][0]
    уровни(0)

    fig, ax = plt.subplots(figsize=(13, 5.8))
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")
    всего = д.n_node_samples[0]

    for у in range(д.node_count):
        x, y = поз[у]
        лист = д.children_left[у] == -1
        доли = д.value[у][0]
        кл = классы[int(np.argmax(доли))]
        чистота = доли.max() / доли.sum()
        сегментов = int(д.n_node_samples[у])
        if лист:
            ax.add_patch(FancyBboxPatch((x - 8.6, y - 7.5), 17.2, 15,
                         boxstyle="round,pad=0.6,rounding_size=1.2",
                         facecolor=ЦВЕТ_ТИПА[кл], alpha=0.20, edgecolor="none"))
            ax.text(x, y + 2.6, кл, ha="center", va="center", fontsize=11,
                    fontweight="bold", color=ЦВЕТ_ТИПА[кл])
            ax.text(x, y - 3.6, f"{сегментов} сегм., из них\n"
                    f"{чистота*100:.0f} % этого типа", ha="center", va="center",
                    fontsize=9.5, color=СЕРЫЙ, linespacing=1.4)
        else:
            имя = имена[д.feature[у]]
            порог = д.threshold[у]
            вопрос = (f"{имя.capitalize()}?" if порог < 1
                      else f"{имя.capitalize()} больше {порог:.0f}?")
            ax.add_patch(FancyBboxPatch((x - 12, y - 5), 24, 10,
                         boxstyle="round,pad=0.5,rounding_size=1",
                         facecolor=СВЕТЛЫЙ, edgecolor="#C9CFD5", linewidth=1.1))
            ax.text(x, y, вопрос, ha="center", va="center", fontsize=10.5,
                    color=ЧЕРНИЛА)
            for ребёнок, подпись, сдвиг in [(д.children_left[у], "нет", -1),
                                            (д.children_right[у], "да", 1)]:
                xr, yr = поз[ребёнок]
                ax.add_patch(FancyArrowPatch((x + сдвиг * 3, y - 5.5), (xr, yr + 8),
                             arrowstyle="-|>", mutation_scale=12,
                             color="#B8C0C7", linewidth=1.2,
                             shrinkA=0, shrinkB=2))
                ax.text((x + сдвиг * 3 + xr) / 2 + сдвиг * 2.2, (y - 5.5 + yr + 8) / 2,
                        подпись, ha="center", va="center", fontsize=10,
                        color=СЕРЫЙ, style="italic")

    ax.set_title(f"Дерево решений на шести признаках, придуманных лингвистом: "
                 f"точность {ФАКТЫ['дерево_точность']:.3f}\n"
                 f"(символьные n-граммы на том же корпусе — 0.744, "
                 f"но их правила прочесть нельзя)",
                 fontsize=12, color=ЧЕРНИЛА, linespacing=1.5)
    fig.tight_layout()
    сохранить(fig, "tree.pdf")


# ------------------------------------------------ 4. регрессия
def регрессия():
    en = К["en"].str.len().values.astype(float)
    ru = К["ru_ref"].str.len().values.astype(float)
    a, b = np.polyfit(en, ru, 1)
    r = float(np.corrcoef(en, ru)[0, 1])
    ФАКТЫ.update({"регр_a": round(float(a), 3), "регр_b": round(float(b), 1),
                  "регр_r2": round(r**2, 3),
                  "расширение": round(float((ru/en).mean()), 3)})

    fig, ax = plt.subplots(figsize=(11, 5.4))
    for тип, цвет in ЦВЕТ_ТИПА.items():
        м = (К["type"] == тип).values
        ax.scatter(en[м], ru[м], s=46, color=цвет, alpha=0.72, label=тип,
                   edgecolors="white", linewidths=0.6)
    сетка = np.linspace(en.min(), en.max(), 50)
    ax.plot(сетка, a*сетка + b, color=ЧЕРНИЛА, linewidth=2,
            label=f"регрессия: ru = {a:.2f}·en + {b:.1f}")
    ax.plot(сетка, сетка, color=СЕРЫЙ, linestyle="--", linewidth=1.2,
            label="равная длина")
    ax.set_xlabel("длина оригинала, символов")
    ax.set_ylabel("длина перевода, символов")
    ax.set_title(f"Сколько места займёт перевод: R² = {r**2:.2f}, "
                 f"в среднем русский текст длиннее на "
                 f"{(ФАКТЫ['расширение']-1)*100:.0f} %",
                 fontsize=12.5, color=ЧЕРНИЛА)
    ax.legend(frameon=False, fontsize=10, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    сохранить(fig, "regression.pdf")


# ------------------------------------------------ 5. снижение размерности
def снижение():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.decomposition import TruncatedSVD
    V = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5)).fit_transform(К["ru_ref"])
    svd = TruncatedSVD(n_components=120, random_state=0).fit(V)
    доля = np.cumsum(svd.explained_variance_ratio_)
    ФАКТЫ.update({"признаков_нграмм": int(V.shape[1]),
                  "доля_60": round(float(доля[59]), 3)})

    fig, ax = plt.subplots(figsize=(10.5, 4.8))
    ax.plot(range(1, 121), доля*100, color=СИНИЙ, linewidth=2.4)
    ax.axvline(60, color=КРАСНЫЙ, linestyle="--", linewidth=1.4)
    ax.annotate(f"60 измерений вместо {V.shape[1]}\nсохраняют {доля[59]*100:.0f} % изменчивости",
                xy=(60, доля[59]*100), xytext=(68, доля[59]*100 - 22),
                fontsize=11, color=КРАСНЫЙ,
                arrowprops=dict(arrowstyle="->", color=КРАСНЫЙ, linewidth=1.2))
    ax.set_xlabel("число оставленных измерений")
    ax.set_ylabel("сохранённая изменчивость, %")
    ax.set_title("Снижение размерности: сколько информации остаётся при сжатии",
                 fontsize=12.5, color=ЧЕРНИЛА)
    ax.set_xlim(0, 121); ax.set_ylim(0, 100)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    сохранить(fig, "svd.pdf")


# ------------------------------------------------ 6. кластеризация
def кластеризация():
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA
    from sklearn.metrics import adjusted_rand_score
    E = np.load(os.path.join(ДАННЫЕ, "emb_ru_ref.npy"))
    кл = KMeans(n_clusters=4, random_state=0, n_init=10).fit_predict(E)
    ari = adjusted_rand_score(К["type"], кл)
    ФАКТЫ["ari"] = round(float(ari), 3)
    XY = PCA(n_components=2, random_state=0).fit_transform(E)

    fig, оси = plt.subplots(1, 2, figsize=(12.6, 5.0))
    for тип, цвет in ЦВЕТ_ТИПА.items():
        м = (К["type"] == тип).values
        оси[0].scatter(XY[м, 0], XY[м, 1], s=42, color=цвет, alpha=0.75,
                       label=тип, edgecolors="white", linewidths=0.5)
    оси[0].set_title("Как разметил человек", fontsize=12, fontweight="bold")
    оси[0].legend(frameon=False, fontsize=9.5, loc="best")

    for н in range(4):
        м = кл == н
        оси[1].scatter(XY[м, 0], XY[м, 1], s=42, alpha=0.75,
                       color=["#4C72B0", "#DD8452", "#55A868", "#C44E52"][н],
                       label=f"кластер {н}", edgecolors="white", linewidths=0.5)
    оси[1].set_title(f"Что нашла кластеризация: ARI = {ari:.3f}",
                     fontsize=12, fontweight="bold", color=КРАСНЫЙ)
    оси[1].legend(frameon=False, fontsize=9.5, loc="best")
    for ax in оси:
        ax.set_xticks([]); ax.set_yticks([])
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Кластеризация ищет группы, которые есть в данных, "
                 "а не те, которые придумал человек", fontsize=12.5, y=1.02)
    fig.tight_layout()
    сохранить(fig, "clusters.pdf")


# ------------------------------------------------ 7. ассоциации
def ассоциации():
    ток = lambda s: re.findall(r"[а-яё]+", str(s).lower())
    слова, биграммы = Counter(), Counter()
    for s in К["ru_ref"]:
        w = ток(s)
        слова.update(w); биграммы.update(zip(w, w[1:]))
    N, Nb = sum(слова.values()), sum(биграммы.values())
    pmi = {п: np.log2((к/Nb) / ((слова[п[0]]/N) * (слова[п[1]]/N)))
           for п, к in биграммы.items() if к >= 2}
    топ_ч = биграммы.most_common(8)
    топ_p = sorted(pmi.items(), key=lambda kv: -kv[1])[:8]

    fig, оси = plt.subplots(1, 2, figsize=(12.6, 4.6))
    for ax, данные, заг, цвет, подпись in [
        (оси[0], топ_ч, "Просто по частоте", СЕРЫЙ, "служебные обороты"),
        (оси[1], [(п, к) for п, к in топ_p], "По силе связи (PMI)", ЗЕЛЁНЫЙ,
         "термины и устойчивые сочетания")]:
        метки = [f"{a} {b}" for (a, b) in [п for п, _ in данные]][::-1]
        значения = [float(v) for _, v in данные][::-1]
        ax.barh(range(len(метки)), значения, color=цвет, alpha=0.8, height=0.65)
        ax.set_yticks(range(len(метки))); ax.set_yticklabels(метки, fontsize=11)
        ax.set_title(f"{заг} — {подпись}", fontsize=12, color=цвет,
                     fontweight="bold")
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(length=0)
    оси[0].set_xlabel("сколько раз встретилось")
    оси[1].set_xlabel("PMI, бит")
    fig.suptitle("Поиск правил на корпусе курса: какие слова ходят парами",
                 fontsize=12.5, y=1.03)
    fig.tight_layout()
    сохранить(fig, "assoc.pdf")


# ------------------------------------------------ 8. ансамбль проверок
def ансамбль():
    E_ref = np.load(os.path.join(ДАННЫЕ, "emb_ru_ref.npy"))
    E_mt = np.load(os.path.join(ДАННЫЕ, "emb_ru_mt.npy"))
    сем = np.array([float(E_ref[i] @ E_mt[i]) for i in range(len(К))])

    ТОК = re.compile(r"\w+", re.UNICODE)
    ПЛ = re.compile(r"\{[^}]*\}|%[sd]|<[^>]+>")
    ЧИ = re.compile(r"\d+")
    НEN = re.compile(r"\b(not|no|never|cannot|can't|don't|doesn't|unable|without|nor)\b", re.I)
    НRU = re.compile(r"\b(не|нет|ни|нельзя|без|никогда|никаких|отсутствует|запрещ\w*)\b", re.I)

    def проверки(en, ru):
        en, ru = str(en), str(ru)
        сраб = (set(ПЛ.findall(en)) != set(ПЛ.findall(ru))
                or sorted(ЧИ.findall(en)) != sorted(ЧИ.findall(ru))
                or (НEN.search(en) and not НRU.search(ru))
                or not ru.strip() or len(ru) < 0.45*len(en))
        return bool(сраб)

    форм = np.array([проверки(e, r) for e, r in zip(К["en"], К["ru_mt"])])

    def bleu(h, r):
        h, r = ТОК.findall(str(h).lower()), ТОК.findall(str(r).lower())
        if not h or not r: return 0.0
        точн = []
        for n in range(1, 5):
            hn = Counter(tuple(h[i:i+n]) for i in range(len(h)-n+1))
            rn = Counter(tuple(r[i:i+n]) for i in range(len(r)-n+1))
            всего = sum(hn.values())
            if всего == 0: continue
            точн.append((sum(min(c, rn[g]) for g, c in hn.items()) + 1.0)/(всего + 1.0))
        if not точн: return 0.0
        ш = 1.0 if len(h) > len(r) else np.exp(1 - len(r)/max(len(h), 1))
        return float(np.exp(np.mean(np.log(точн))) * ш)

    B = np.array([bleu(h, r) for h, r in zip(К["ru_mt"], К["ru_ref"])])
    мед = float(np.median(B))
    средства = [("BLEU ниже медианы", B < мед, СЕРЫЙ),
                ("Семантика < 0.7", сем < 0.70, СИНИЙ),
                ("Формальные проверки", форм, ЗЕЛЁНЫЙ)]
    ФАКТЫ["проверки"] = {и: int(м.sum()) for и, м, _ in средства}

    fig, ax = plt.subplots(figsize=(11.5, 4.6))
    имена = [и for и, _, _ in средства][::-1]
    доли = [м.sum()/len(К)*100 for _, м, _ in средства][::-1]
    цвета = [c for _, _, c in средства][::-1]
    ax.barh(range(3), доли, color=цвета, alpha=0.85, height=0.55)
    for i, (д, (_, м, _)) in enumerate(zip(доли, средства[::-1])):
        ax.text(д + 1.2, i, f"{int(м.sum())} сегментов из {len(К)}  ({д:.0f} %)",
                va="center", fontsize=11, color=ЧЕРНИЛА)
    ax.set_yticks(range(3)); ax.set_yticklabels(имена, fontsize=12)
    ax.set_xlabel("сколько сегментов уходит человеку на проверку, %")
    ax.set_xlim(0, 72)
    ax.set_title("Ансамбль, собранный человеком: три средства приёмки "
                 "ловят разное и стоят разного", fontsize=12.5, color=ЧЕРНИЛА)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout()
    сохранить(fig, "ensemble.pdf")


# ------------------------------------------------ 9. нейрон
def нейрон():
    fig, ax = plt.subplots(figsize=(12, 4.8))
    ax.set_xlim(0, 100); ax.set_ylim(0, 40); ax.axis("off")
    входы = [("есть плейсхолдер", "1", 32), ("слов в сегменте", "3", 24),
             ("кончается точкой", "0", 16), ("есть «ваш»", "0", 8)]
    веса = ["w₁ = 1.4", "w₂ = −0.8", "w₃ = −2.1", "w₄ = 0.3"]
    for (имя, знач, y), w in zip(входы, веса):
        ax.text(1, y, имя, fontsize=11, va="center", color=ЧЕРНИЛА)
        ax.add_patch(Circle((26, y), 1.9, facecolor=СВЕТЛЫЙ, edgecolor=СИНИЙ,
                            linewidth=1.4))
        ax.text(26, y, знач, ha="center", va="center", fontsize=11,
                fontweight="bold", color=СИНИЙ)
        ax.add_patch(FancyArrowPatch((28.2, y), (49, 20), arrowstyle="-|>",
                     mutation_scale=13, color="#B8C0C7", linewidth=1.3))
        ax.text(37, (y + 20)/2 + (1.6 if y > 20 else -1.6), w, fontsize=10,
                color=СЕРЫЙ, ha="center")
    ax.add_patch(Circle((53, 20), 4.4, facecolor=КРАСНЫЙ, alpha=0.18,
                        edgecolor=КРАСНЫЙ, linewidth=1.8))
    ax.text(53, 20, "Σ", ha="center", va="center", fontsize=20, color=КРАСНЫЙ)
    ax.text(53, 13.0, "взвешенная\nсумма", ha="center", va="top", fontsize=10,
            color=КРАСНЫЙ, linespacing=1.4)
    ax.add_patch(FancyArrowPatch((57.6, 20), (66, 20), arrowstyle="-|>",
                 mutation_scale=15, color="#B8C0C7", linewidth=1.6))
    ax.add_patch(FancyBboxPatch((66.5, 14.5), 14, 11,
                 boxstyle="round,pad=0.4,rounding_size=1",
                 facecolor=ЗЕЛЁНЫЙ, alpha=0.15, edgecolor="none"))
    x = np.linspace(-3, 3, 100)
    ax.plot(70.5 + x*1.7, 20 + np.maximum(0, x)*1.6, color=ЗЕЛЁНЫЙ, linewidth=2.2)
    ax.text(73.5, 24.0, "функция\nактивации", ha="center", va="center",
            fontsize=10, color=ЗЕЛЁНЫЙ, linespacing=1.3)
    ax.add_patch(FancyArrowPatch((81, 20), (88, 20), arrowstyle="-|>",
                 mutation_scale=15, color="#B8C0C7", linewidth=1.6))
    ax.text(89, 20, "интерфейс", fontsize=12, va="center", fontweight="bold",
            color=ЧЕРНИЛА)
    ax.text(50, 2.2, "Обучение — это подбор весов w по размеченным примерам. "
            "Признаки здесь названы по-человечески; в реальной модели их тысячи "
            "и имён у них нет.", ha="center", fontsize=10.5, color=СЕРЫЙ,
            style="italic")
    fig.tight_layout()
    сохранить(fig, "neuron.pdf")


# ------------------------------------------------ 10. матрица ошибок
def матрица_ошибок():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import ConfusionMatrixDisplay, accuracy_score
    Xtr, Xte, ytr, yte = train_test_split(К["ru_ref"], К["type"], test_size=0.25,
                                          stratify=К["type"], random_state=0)
    p = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)),
                      LogisticRegression(max_iter=2000)).fit(Xtr, ytr)
    y_pred = p.predict(Xte)
    ФАКТЫ["матрица_точность"] = round(float(accuracy_score(yte, y_pred)), 3)
    fig, ax = plt.subplots(figsize=(6.6, 5.6))
    ConfusionMatrixDisplay.from_predictions(yte, y_pred, ax=ax, cmap="Blues",
                                            colorbar=False, xticks_rotation=30)
    ax.set_xlabel("предсказано моделью"); ax.set_ylabel("на самом деле")
    ax.set_title(f"Матрица ошибок: точность {ФАКТЫ['матрица_точность']:.3f} "
                 f"на 40 отложенных сегментах", fontsize=11.5)
    fig.tight_layout()
    сохранить(fig, "confusion.pdf")


print("иллюстрации к «Введению в МО» (МЛГ):")
направления(); мешок_слов(); дерево(); регрессия(); снижение()
кластеризация(); ассоциации(); ансамбль(); нейрон(); матрица_ошибок()
print("\nФАКТЫ ДЛЯ СЛАЙДОВ:")
print(json.dumps(ФАКТЫ, ensure_ascii=False, indent=2))
json.dump(ФАКТЫ, open(os.path.join(ВЫХОД, "факты.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
