"""fig_threshold.py — 前刷の図：しきい値が「どんな入力で」現れるか（DF）

  (a) 1 s 保持したあとの小ステップ：大きさ別の 50 % 到達時間（指令から、ms）。実機と流量上限モデル O（10〜80 kPa）
  (b) 振幅 40 kPa（中心 0.30 MPa）の持続した正弦：周波数別の振幅比。実機・O・しきい値モデル A
  → 止まった状態からの小ステップは大きさによらず届くが、持続した振動は周波数で通らなくなる

使い方: python analysis/itv/fig_threshold.py
出力:   data/user0/jfps2026/figures_paper/fig_threshold.{pdf,png}
"""
import os, sys, json, numpy as np, pandas as pd
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import pmeval as E  # noqa: E402
from base_eval import sim_O  # noqa: E402
from model_A import shape_cmd  # noqa: E402
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt  # noqa: E402

OUT = os.path.join(E.JP_DEFAULT, "data", "user0", "jfps2026", "figures_paper"); os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"font.family": "serif", "font.serif": ["TeX Gyre Termes", "Times New Roman", "DejaVu Serif"],
                     "mathtext.fontset": "stix", "font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
                     "lines.linewidth": 1.1, "lines.markersize": 3.5})
A = json.load(open(os.path.join(_HERE, "fit_A.json")))
O = np.array(A["O"]); fg, ag, G = np.array(A["fg"]), np.array(A["ag"]), np.array(A["G"])
simO = lambda u, p0: sim_O(u, p0, *O)
simA = lambda u, p0: sim_O(shape_cmd(u, A["tc"], A["W"], A["hk"], A["h0"], fg, ag, G), p0, *O)

# (a) 小ステップ
s = E.load(E.JP_DEFAULT, "smallstep"); p0 = float(np.median(s["m"][:20]))
tr = E.smallstep_t50(s["u"], s["m"], s["seg"]); to = E.smallstep_t50(s["u"], simO(s["u"], p0), s["seg"])
# (b) 40 kPa の正弦
D = {k: E.load(E.JP_DEFAULT, k) for k in ("ampsweep", "sine", "sine2")}
pO = {k: simO(d["u"], float(np.median(d["m"][:20]))) for k, d in D.items()}
pA = {k: simA(d["u"], float(np.median(d["m"][:20]))) for k, d in D.items()}
keys = ("ampsweep", "sine", "sine2")
TO = E.sine_table(D, pO, keys=keys); TA = E.sine_table(D, pA, keys=keys)
sel = lambda T: T[np.isclose(T.a, 0.04) & T.c.between(0.25, 0.35)].groupby("f")[["real", "model"]].mean()
mO, mA = sel(TO), sel(TA)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(3.3, 1.75))
for d, mk, lab in (("dn", "o", "down"), ("up", "s", "up")):
    ks = sorted(k for k in tr if k[0] == d and k[1] >= 0.01)   # 5 kPa は 50 % がノイズ幅（約 2.5 kPa）に埋もれるので除く
    x = [k[1] * 1000 for k in ks]
    ax1.plot(x, [tr[k] for k in ks], mk + "-", color="#1b2430", label=f"Measured ({lab})", mfc="white" if d == "up" else None)
ks = sorted(k for k in to if k[0] == "dn" and k[1] >= 0.01)
ax1.plot([k[1] * 1000 for k in ks], [to[k] for k in ks], "--", color="#8a8985", label="Model O")
ax1.set_xscale("log"); ax1.set_xticks([10, 20, 40, 80]); ax1.set_xticklabels(["10", "20", "40", "80"]); ax1.minorticks_off()
ax1.set_ylim(0, 300); ax1.set_xlabel("Step size [kPa]"); ax1.set_ylabel("Time to 50% [ms]")
ax1.set_title("(a) Isolated steps", loc="left", fontsize=8); ax1.legend(frameon=False, fontsize=6, loc="upper left", handlelength=1.6)
ax2.plot(mO.index, mO.real, "o-", color="#1b2430", label="Measured")
ax2.plot(mO.index, mO.model, "--", color="#8a8985", label="Model O")
ax2.plot(mA.index, mA.model, "-", color="#c2410c", label="O + threshold")
ax2.set_ylim(0, 1.4); ax2.set_xlabel("Frequency [Hz]"); ax2.set_ylabel("Amplitude ratio")
ax2.set_title("(b) Sustained sine, 40 kPa", loc="left", fontsize=8); ax2.legend(frameon=False, fontsize=6, loc="upper right")
for ax in (ax1, ax2):
    ax.grid(alpha=0.25, lw=0.5)
fig.tight_layout(w_pad=0.8)
for ext in ("pdf", "png"):
    fig.savefig(os.path.join(OUT, f"fig_threshold.{ext}"), dpi=200)
print("t50 [ms] real:", {k: round(v) for k, v in sorted(tr.items())})
print("t50 [ms] O   :", {k: round(v) for k, v in sorted(to.items())})
print("40 kPa amplitude ratio"); print(pd.concat([mO.rename(columns={"model": "O"}), mA[["model"]].rename(columns={"model": "A"})], axis=1).round(2).to_string())
