# -*- coding: utf-8 -*-
"""Иллюстрации для МЛГ-версии презентации «Интеллектуальный анализ текстовых данных»."""
import os, sys, json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

ВЫХОД = sys.argv[1]
ДАННЫЕ = sys.argv[2]
os.makedirs(ВЫХОД, exist_ok=True)

СИНИЙ, КРАСНЫЙ, ЗЕЛЁНЫЙ = "#2B4C6F", "#B03A2B", "#3E7D5A"
СЕРЫЙ, ЧЕРНИЛА = "#8B9299", "#1B2433"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12,
                     "axes.edgecolor": "#C9CFD5", "axes.labelcolor": ЧЕРНИЛА,
                     "text.color": ЧЕРНИЛА, "xtick.color": СЕРЫЙ,
                     "ytick.color": СЕРЫЙ, "figure.facecolor": "white"})


def сохранить(fig, имя):
    путь = os.path.join(ВЫХОД, имя)
    fig.savefig(путь, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  ", имя, f"{os.path.getsize(путь)/1024:.0f} КБ")
    return путь


# ---------------------------------------------------------------- 1. активации
def активации():
    x = np.linspace(-4, 4, 600)
    шаг = (x > 0).astype(float)
    сигмоида = 1 / (1 + np.exp(-x))
    tanh = np.tanh(x)
    relu = np.maximum(0, x)
    gelu = 0.5 * x * (1 + np.tanh(np.sqrt(2 / np.pi) * (x + 0.044715 * x ** 3)))

    fig, оси = plt.subplots(1, 2, figsize=(12, 4.4))

    старые = [("единичный скачок", шаг), ("сигмоида", сигмоида),
              ("гиперболический тангенс", tanh)]
    for (имя, y), ц in zip(старые, [СЕРЫЙ, "#7A93AC", СИНИЙ]):
        оси[0].plot(x, y, label=имя, linewidth=2.2, color=ц)
    оси[0].set_title("Что на слайде: до 2012 года", color=СЕРЫЙ, fontsize=13)

    новые = [("ReLU", relu), ("GELU", gelu)]
    for (имя, y), ц in zip(новые, [КРАСНЫЙ, ЗЕЛЁНЫЙ]):
        оси[1].plot(x, y, label=имя, linewidth=2.6, color=ц)
    оси[1].plot(x, сигмоида, linewidth=1.4, color=СЕРЫЙ, linestyle="--",
                label="сигмоида (для сравнения)")
    оси[1].set_title("Что используют сейчас", color=ЧЕРНИЛА, fontsize=13,
                     fontweight="bold")

    for ax in оси:
        ax.axhline(0, color="#DDE2E6", linewidth=1)
        ax.axvline(0, color="#DDE2E6", linewidth=1)
        ax.set_xlim(-4, 4); ax.set_ylim(-1.4, 4)
        ax.legend(frameon=False, fontsize=11, loc="upper left")
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return сохранить(fig, "fig_активации.png")


# ---------------------------------------------------------------- 2. токенизация
def токенизация():
    fig, ax = plt.subplots(figsize=(12, 4.6))
    ax.set_xlim(0, 100); ax.set_ylim(0, 46); ax.axis("off")

    ряды = [
        ("по словам", ["локализации"], СЕРЫЙ,
         "1 признак; «локализация» и «локализации» — разные слова"),
        ("по символам", list("локализации"), "#7A93AC",
         "11 признаков; морфология потеряна, контекст тратится на буквы"),
        ("по подсловам (BPE)", ["локал", "иза", "ции"], КРАСНЫЙ,
         "3 признака; корень отделён от окончания — так работает GPT"),
    ]
    y = 36
    for имя, куски, цвет, подпись in ряды:
        ax.text(0, y + 6.5, имя, fontsize=13, fontweight="bold", color=ЧЕРНИЛА)
        x = 0
        # ширина блока подгоняется под длину куска, чтобы текст не вылезал
        ширины = [max(5.5, 2.9 + 2.1 * len(к)) for к in куски]
        общая = sum(ширины) + 0.7 * (len(куски) - 1)
        if общая > 92:                       # не выходим за поле рисунка
            k = 92 / общая
            ширины = [w * k for w in ширины]
        for к, ш in zip(куски, ширины):
            ax.add_patch(FancyBboxPatch((x, y - 1.2), ш, 6,
                         boxstyle="round,pad=0.25,rounding_size=0.6",
                         facecolor=цвет, edgecolor="none", alpha=0.16))
            ax.text(x + ш / 2, y + 1.8, к, ha="center", va="center",
                    fontsize=13 if len(к) > 1 else 12, color=цвет,
                    fontweight="bold")
            x += ш + 0.7
        ax.text(0, y - 4.2, подпись, fontsize=11, color=СЕРЫЙ, style="italic")
        y -= 15
    fig.tight_layout()
    return сохранить(fig, "fig_токенизация.png")


# ---------------------------------------------------------------- 3. конвейер
def конвейер():
    fig, ax = plt.subplots(figsize=(12, 3.9))
    ax.set_xlim(0, 100); ax.set_ylim(0, 30); ax.axis("off")

    блоки = [
        ("Предобучение", "миллиарды слов\nмесяцы вычислений\nмиллионы долларов",
         СЕРЫЙ, "вам недоступно\nи не нужно"),
        ("Дообучение", "тысячи примеров\nчасы вычислений\nваша задача",
         СИНИЙ, "иногда делают\nв компании"),
        ("Применение", "готовая модель\nваши данные\nоценка качества",
         КРАСНЫЙ, "здесь работает\nлингвист"),
    ]
    x = 2
    ш, в = 27, 13
    for i, (имя, тело, цвет, снизу) in enumerate(блоки):
        ax.add_patch(FancyBboxPatch((x, 12), ш, в,
                     boxstyle="round,pad=0.4,rounding_size=1",
                     facecolor=цвет, edgecolor="none",
                     alpha=0.14 if i < 2 else 0.20))
        ax.text(x + ш / 2, 22.2, имя, ha="center", fontsize=14,
                fontweight="bold", color=цвет)
        ax.text(x + ш / 2, 16.6, тело, ha="center", va="center",
                fontsize=11, color=ЧЕРНИЛА, linespacing=1.5)
        ax.text(x + ш / 2, 8.4, снизу, ha="center", va="top", fontsize=11,
                color=цвет, style="italic", linespacing=1.4,
                fontweight="bold" if i == 2 else "normal")
        if i < 2:
            ax.add_patch(FancyArrowPatch((x + ш + 1, 18.5), (x + ш + 6, 18.5),
                         arrowstyle="-|>", mutation_scale=22,
                         color="#B8C0C7", linewidth=2))
        x += ш + 7
    fig.tight_layout()
    return сохранить(fig, "fig_конвейер.png")


# ---------------------------------------------------------------- 4. выбросы
def выбросы():
    import pandas as pd
    from sklearn.decomposition import PCA
    корпус = pd.read_csv(os.path.join(ДАННЫЕ, "loc_corpus.csv"))
    E = np.load(os.path.join(ДАННЫЕ, "emb_ru_ref.npy"))

    центр = E.mean(axis=0)
    центр /= np.linalg.norm(центр)
    расстояние = 1 - E @ центр
    порог = np.percentile(расстояние, 95)
    выброс = расстояние >= порог

    XY = PCA(n_components=2, random_state=0).fit_transform(E)
    fig, ax = plt.subplots(figsize=(11, 5.6))
    ax.scatter(XY[~выброс, 0], XY[~выброс, 1], s=52, color=СИНИЙ, alpha=0.42,
               edgecolors="white", linewidths=0.7, label="обычные сегменты")
    ax.scatter(XY[выброс, 0], XY[выброс, 1], s=140, color=КРАСНЫЙ,
               edgecolors="white", linewidths=1.2, zorder=5,
               label=f"кандидаты в выбросы (верхние 5 %)")

    подписано = 0
    for i in np.argsort(-расстояние):
        if not выброс[i] or подписано >= 4:
            continue
        т = корпус["ru_ref"].iloc[i]
        т = т if len(т) <= 34 else т[:32] + "…"
        сдвиг = [(11, 9), (11, -16), (-11, 9), (-11, -16)][подписано]
        ax.annotate(т, (XY[i, 0], XY[i, 1]),
                    textcoords="offset points", xytext=сдвиг,
                    ha="left" if сдвиг[0] > 0 else "right",
                    fontsize=10, color=КРАСНЫЙ,
                    bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                              edgecolor="none", alpha=0.82))
        подписано += 1

    ax.set_xlabel("главная компонента 1")
    ax.set_ylabel("главная компонента 2")
    ax.set_title("Корпус в пространстве эмбеддингов: чем дальше от центра, "
                 "тем вероятнее выброс", fontsize=12.5, color=ЧЕРНИЛА)
    ax.legend(frameon=False, fontsize=11, loc="best")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    путь = сохранить(fig, "fig_выбросы.png")
    print("     помечено выбросов:", int(выброс.sum()), "из", len(корпус))
    return путь


print("иллюстрации:")
активации(); токенизация(); конвейер(); выбросы()
print("готово")
