"""O / A / B / B＋漏れ の比較図（振幅比マップと時間波形）"""
import os, sys, json, numpy as np, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import pmeval as E
from base_eval import sim_O
from model_A import shape_cmd
from model_B import sim_B
from model_BL import sim_BL
plt.rcParams.update({"font.family": "Noto Sans CJK JP", "font.size": 12, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": "#e6e5e0"})
A = json.load(open(_HERE + "/fit_A.json")); B = json.load(open(_HERE + "/fit_B2_w0.7.json")); BL = json.load(open(_HERE + "/fit_BL_w0.7.json"))
O = np.array(A["O"]); fg, ag, G = np.array(A["fg"]), np.array(A["ag"]), np.array(A["G"])
thB = [B[k] for k in ["L", "ki", "kpp", "d", "km", "cin", "cout", "ps", "b", "km_dn"]]
sims = {"O（流量上限）": lambda u, p0: sim_O(u, p0, *O),
        "A（現象論）": lambda u, p0: sim_O(shape_cmd(u, A["tc"], A["W"], A["hk"], A["h0"], fg, ag, G), p0, *O),
        "B（パイロット段）": lambda u, p0: sim_B(u, p0, *thB),
        "B＋漏れ": lambda u, p0: sim_BL(u, p0, *[BL[k] for k in ["L", "ki", "kpp", "d", "km", "cin", "cout", "ps", "b", "km_dn", "kl"]])}
col = {"実機": "#1b2430", "O（流量上限）": "#8a8985", "A（現象論）": "#2a78d6", "B（パイロット段）": "#e8b48f", "B＋漏れ": "#c2410c"}
D = E.load_all()
res = {k: E.evaluate(D, s) for k, s in sims.items()}
fig = plt.figure(figsize=(14, 9)); gs = fig.add_gridspec(2, 3)
for j, a in enumerate((0.02, 0.04, 0.12)):
    ax = fig.add_subplot(gs[0, j])
    for k, r in res.items():
        T = r["map"]; T = T[(T.c == 0.3) & (T.a == a) & T.data.isin(["ampsweep", "sine2", "sine"])].groupby("f")[["real", "model"]].mean()
        if k == "O（流量上限）":
            ax.plot(T.index, T.real, "o-", color=col["実機"], lw=2.2, label="実機")
        ax.plot(T.index, T.model, "s--" if k != "O（流量上限）" else ":", color=col[k], lw=1.8, label=k)
    ax.set_title(f"振幅 {a*1000:.0f} kPa（中心 0.30 MPa）", loc="left"); ax.set_xlabel("周波数 [Hz]"); ax.set_ylim(0, 1.5)
    if j == 0: ax.set_ylabel("振幅比（出力 / 指令）"); ax.legend(frameon=False, fontsize=10)
for j, (key, seg_or_t, title) in enumerate([("sine2", "amp_c0.3_a0.04_f4.0", "4 Hz・振幅 40 kPa の正弦"),
                                              ("tmE2", (7.0, 9.0), "打撃指令の再生（同定に不使用）")]):
    ax = fig.add_subplot(gs[1, j * 1 if j == 0 else 1:3] if j == 1 else gs[1, 0])
    d = D[key]
    if isinstance(seg_or_t, str):
        ii = np.flatnonzero(d["seg"] == seg_or_t); ii = ii[len(ii) // 2: len(ii) // 2 + 300]
    else:
        ii = np.arange(int(seg_or_t[0] / 0.005), int(seg_or_t[1] / 0.005))
    t = (ii - ii[0]) * 0.005
    ax.plot(t, d["u"][ii] * 1000, color="#c9c7c0", lw=1, label="指令")
    ax.plot(t, d["m"][ii] * 1000, color=col["実機"], lw=2, label="実機")
    for k, s in sims.items():
        p = s(d["u"], float(np.median(d["m"][:20])))
        ax.plot(t, p[ii] * 1000, color=col[k], lw=1.5, ls=":" if k == "O（流量上限）" else "-", label=k)
    ax.set_title(title, loc="left"); ax.set_xlabel("時間 [s]"); ax.set_ylabel("圧力 [kPa]")
    if j == 1: ax.legend(frameon=False, fontsize=10, ncol=6, loc="upper left")
fig.tight_layout(); fig.savefig(_HERE + "/models_compare.png", dpi=130)
import pandas as pd
print(pd.DataFrame([E.summary(r, k) for k, r in res.items()]).to_string())
