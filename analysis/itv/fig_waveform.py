"""fig_waveform.py — 前刷の図：従来モデルと流量上限モデルの圧力波形（table1_models.py と同じモデル・同じ遅れ δ）

  (a) 2月のチャープ（時間の伸びを補正）の速い部分
  (b) 打撃中の実機ログ 1 本（RA-L、ral_20260803）の DF
  実線＝実測、灰＝従来（T）、色＝流量上限（O）

使い方: python analysis/itv/fig_waveform.py [<jetson_project>]
出力:   data/user0/jfps2026/figures_paper/fig_waveform.{pdf,png}
"""
import os, sys, json, numpy as np, pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
JP = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("JETSON_PROJECT", os.path.abspath(os.path.join(_HERE, "..", "..")))
os.chdir(JP); sys.path.insert(0, "analysis")
src = open("analysis/compare_pressure_models.py").read().split("# ------------------------------------------------------------------ fit on")[0]
exec(src.replace("cache=True", "cache=False"))
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt  # noqa: E402

OUT = os.path.join(JP, "data", "user0", "jfps2026", "figures_paper"); os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"font.family": "serif", "font.serif": ["TeX Gyre Termes", "Times New Roman", "DejaVu Serif"],
                     "font.size": 8, "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.0})
FITS = json.load(open(os.path.join(_HERE, "struct_vs_data_fits.json")))["step"]
TH = {"T": None, "O": np.array(FITS["O"])}
DELTA = {"feb": {"T": 25, "O": 35}, "ral": {"T": 40, "O": 45}}      # table1_models.py で選ばれた δ [ms]
COL = {"T": "#8a8985", "O": "#c2410c"}
LAB = {"T": "Table model (T)", "O": "Lag + flow limit (O)"}

feb = {e: (u, m) for e, u, m in load_feb()}
u, m = feb["exp3_frequency_sweep"]
ral = load_ral()
k_run = 0                                                            # 1 本目の DF（ファイル名順）
ch, ur, mr = ral[3 * k_run]
assert ch == "DF"


def window_for_chirp(u, length=2.0):
    """チャープの最後（速い側）から length 秒。指令が動いている区間で探す"""
    on = np.flatnonzero(np.abs(np.diff(u)) > 1e-4)
    end = on[-1]; return np.arange(int(end - length / DT), end)


def window_busy(u, length=1.5):
    """指令の上げ下げが最も多い length 秒"""
    n = int(length / DT); d = np.abs(np.diff(u)) > 0.02
    c = np.convolve(d, np.ones(n), "valid"); i0 = int(np.argmax(c)); return np.arange(i0, i0 + n)


fig, axs = plt.subplots(2, 1, figsize=(3.3, 2.6))
for ax, (u_, m_, key, idx, title) in zip(axs, [(u, m, "feb", window_for_chirp(u), "(a) Chirp (fast end)"),
                                                 (ur, mr, "ral", window_busy(ur), "(b) Striking (DF)")]):
    t = (idx - idx[0]) * DT
    ax.plot(t, u_[idx] * 1000, color="#d6d4cd", lw=0.8, label="Command")
    ax.plot(t, m_[idx] * 1000, color="#1b2430", lw=1.4, label="Measured")
    for k in ("T", "O"):
        p = shift(run(k, TH[k], u_), DELTA[key][k] // 5)
        ax.plot(t, p[idx] * 1000, color=COL[k], ls="--" if k == "T" else "-", label=LAB[k])
    ax.set_title(title, loc="left", fontsize=8); ax.set_ylabel("Pressure [kPa]"); ax.grid(alpha=0.25, lw=0.5)
axs[1].set_xlabel("Time [s]")
h, l = axs[0].get_legend_handles_labels()
fig.legend(h, l, frameon=False, fontsize=6, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.0), handlelength=1.6, columnspacing=0.8)
fig.tight_layout(h_pad=0.4, rect=(0, 0, 1, 0.93))
for ext in ("pdf", "png"):
    fig.savefig(os.path.join(OUT, f"fig_waveform.{ext}"), dpi=200)
print("saved fig_waveform")
