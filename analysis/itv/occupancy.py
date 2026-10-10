"""occupancy.py — 打撃中の指令は、電空レギュレータのしきい値の領域をどれだけ使っているか

  RA-L の打撃中ログ 105 本（ral_20260803）の圧力指令を 0.5 s ずつに区切り、区間ごとに
    振幅 A = (最大 − 最小) / 2
    周波数 f = ヒステリシス付きのゼロクロス数 / 2 / 0.5 s（ヒステリシス 0.2A + 3 kPa。model_A.py の推定器と同じ考え方）
  を求めて、次の2通りで分類する。
    ・速さ×大きさ：速い = f ≥ 2 Hz、小さい = A < 50 kPa（振幅比マップで、2 Hz から小振幅が削られ始め、
      3 Hz では 50〜60 kPa まで通らないことから）
    ・しきい値域：モデルAの表 G(f, A)（= 実機 / O の振幅比、fit_A.json）が 0.7 未満の区間
  それぞれの区間が占める時間の割合と、流量上限モデル O の圧力誤差（二乗誤差）の割合を出す。
  O は struct_vs_data_fits.json の "step"、通信の遅れ δ は table1_models.py と同じく全データ共通で 1 つ選ぶ。

使い方: python analysis/itv/occupancy.py [<jetson_project>]
出力:   analysis/itv/occupancy.csv（表）, data/user0/jfps2026/figures_paper/fig_occupancy.{pdf,png}（f × A の占有マップ）
"""
import os, sys, json, numpy as np, pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
JP = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("JETSON_PROJECT", os.path.abspath(os.path.join(_HERE, "..", "..")))
os.chdir(JP); sys.path.insert(0, "analysis")
src = open("analysis/compare_pressure_models.py").read().split("# ------------------------------------------------------------------ fit on")[0]
exec(src.replace("cache=True", "cache=False"))
sys.path.insert(0, _HERE)
from model_A import _interp2  # noqa: E402

O = np.array(json.load(open(os.path.join(_HERE, "struct_vs_data_fits.json")))["step"]["O"])
A_ = json.load(open(os.path.join(_HERE, "fit_A.json")))
FG, AG, GT = np.array(A_["fg"]), np.array(A_["ag"]), np.array(A_["G"])
WIN = 100                    # 0.5 s（200 Hz）
F_FAST, A_SMALL, G_TH = 2.0, 0.05, 0.7


def window_feats(u):
    n = len(u) // WIN; out = []
    for k in range(n):
        w = u[k * WIN:(k + 1) * WIN]; dev = w - w.mean()
        A = (w.max() - w.min()) / 2; h = 0.2 * A + 0.003
        sgn = 1.0 if dev[0] >= 0 else -1.0; nz = 0
        for x in dev:
            if sgn > 0 and x < -h: sgn = -1.0; nz += 1
            elif sgn < 0 and x > h: sgn = 1.0; nz += 1
        out.append((nz / 2 / (WIN * DT), A))
    return np.array(out)


ral = load_ral()
preds = [(run("O", O, u), m) for _, u, m in ral]
errs = [np.mean([np.mean((shift(p, s) - m) ** 2) for p, m in preds]) for s in range(17)]
s_best = int(np.argmin(errs))
rows = []
for (ch, u, m), (p, _) in zip(ral, preds):
    e2 = (shift(p, s_best) - m) ** 2
    F = window_feats(u)
    for k, (f, A) in enumerate(F):
        rows.append(dict(ch=ch, f=f, A=A, err=float(e2[k * WIN:(k + 1) * WIN].sum()),
                         G=float(_interp2(FG, AG, GT, f, A))))
W = pd.DataFrame(rows)
W["速さ"] = np.where(W.f >= F_FAST, "速い", "遅い"); W["大きさ"] = np.where(W.A < A_SMALL, "小さい", "大きい")
W["しきい値域"] = W.G < G_TH


def share(D, key):
    t = D.groupby(key).size() / len(D) * 100; e = D.groupby(key).err.sum() / D.err.sum() * 100
    return pd.DataFrame({"時間 [%]": t, "O の誤差 [%]": e})


out = []
for ch in ("全チャネル", "DF", "F", "G"):
    D = W if ch == "全チャネル" else W[W.ch == ch]
    a = share(D, ["速さ", "大きさ"]); a.index = [f"{i}・{j}" for i, j in a.index]
    b = share(D, "しきい値域").rename(index={True: "しきい値域（G<0.7）", False: "しきい値域の外"})
    c = pd.concat([a, b]); c.insert(0, "チャネル", ch); out.append(c)
R = pd.concat(out)
print(f"105本 × 3チャネル、0.5 s 区間 {len(W)} 個、通信の遅れ δ = {s_best * 5} ms")
print(R.round(1).to_string())
R.to_csv(os.path.join(_HERE, "occupancy.csv"), float_format="%.2f")

try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "serif", "font.serif": ["TeX Gyre Termes", "Times New Roman", "DejaVu Serif"],
                         "font.size": 8})
    OUT = os.path.join(JP, "data", "user0", "jfps2026", "figures_paper"); os.makedirs(OUT, exist_ok=True)
    fig, ax = plt.subplots(figsize=(3.15, 2.3))
    H, xe, ye = np.histogram2d(W.A * 1000, W.f, bins=[np.linspace(0, 300, 31), np.linspace(0, 8, 17)])
    im = ax.pcolormesh(xe, ye, (H / H.sum() * 100).T, cmap="Greys", shading="flat", vmin=0, vmax=2)   # 静止区間（0 Hz・0 kPa）が約7%で目盛りを潰すので 2% で頭打ち
    ff, aa = np.meshgrid(np.linspace(0, 8, 81), np.linspace(0.02, 0.3, 57))
    gg = np.vectorize(lambda f, a: _interp2(FG, AG, GT, f, a))(ff, aa)
    ax.contour(aa * 1000, ff, gg, levels=[G_TH], colors="C3", linewidths=1.5)
    ax.set_xlabel("Command amplitude [kPa]"); ax.set_ylabel("Frequency [Hz]")
    ax.text(40, 6.6, "threshold region\n(model A gain < 0.7)", color="C3", fontsize=7)
    fig.colorbar(im, ax=ax, label="Share of time [%]", extend="max"); fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT, f"fig_occupancy.{ext}"), dpi=200)
except ImportError:
    pass
