"""方策の指令（50 Hz）をより粗い周期でサンプルし直して保持したとき、圧力（流量上限モデル）がどれだけ変わるか
出力: analysis/itv/rate_check.csv、図 data/user0/jfps2026/figures_paper/fig_rate.{pdf,png}
使い方: python rate_check.py [<jetson_project>]  （省略時はこのリポジトリ）"""
import os, sys, json, numpy as np, pandas as pd
_HERE = os.path.dirname(os.path.abspath(__file__))
JP = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("JETSON_PROJECT", os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))); os.chdir(JP); sys.path.insert(0, "analysis")
exec(open("analysis/compare_pressure_models.py").read().split("# ------------------------------------------------------------------ fit on")[0].replace("cache=True", "cache=False"))
O = np.array(json.load(open(os.path.join(_HERE, "struct_vs_data_fits.json")))["step"]["O"])
DT = 0.005
def up200(u50):  # 50 Hz の指令を 200 Hz の零次ホールドに
    return np.repeat(u50, 4)
def resample(u50, fn, phase):
    t = np.arange(len(u50) * 4) * DT
    tk = np.floor((t - phase) * fn) / fn + phase          # 直近の更新時刻
    idx = np.clip((tk / 0.02).astype(int), 0, len(u50) - 1)
    return u50[idx]
def edges(p, lev=0.3):
    return np.flatnonzero((p[:-1] < lev) & (p[1:] >= lev)) * DT
rows = []
for n in ["tm_E_dbl160_seed2", "tm_E_dbl160_seed3", "tm_E_gmd138_seed2", "tm_E_gmd138_seed3"]:
    s = pd.read_csv(f"signals/{n}.csv")
    for c in ["DF", "F", "G"]:
        u50 = s[f"cmd_pressure_{c}"].values
        p0 = run("O", O, up200(u50)); e0 = edges(p0)
        for fn in (33.3, 25, 20, 12.5, 10):
            for ph in np.linspace(0, 1 / fn, 4, endpoint=False):
                p = run("O", O, resample(u50, fn, ph)); e = edges(p)
                sh = [np.min(np.abs(e - x)) for x in e0] if len(e) else [np.nan]
                rows.append(dict(rate=fn, run=n, ch=c, nrmse=np.sqrt(np.mean((p - p0) ** 2)) / np.ptp(p0) * 100,
                                 shift_med=np.median(sh) * 1000, shift_p90=np.percentile(sh, 90) * 1000))
R = pd.DataFrame(rows)
print("50 Hz の指令からの変化（流量上限モデルの圧力。4曲×3チャネル×位相4通り）")
print(R.groupby("rate")[["nrmse", "shift_med", "shift_p90"]].median().round(1).sort_index(ascending=False)
      .rename(columns={"nrmse": "圧力の差 NRMSE[%]", "shift_med": "0.3MPa通過時刻のずれ 中央値[ms]", "shift_p90": "同 90%値[ms]"}).to_string())

# ---- 前刷の図（10/10 追加）：指令周期を粗くしたときの 0.3 MPa 通過時刻のずれ。打撃の判定幅 ±30 ms と比べる
R.to_csv(os.path.join(_HERE, "rate_check.csv"), index=False, float_format="%.2f")
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt  # noqa: E402
plt.rcParams.update({"font.family": "serif", "font.serif": ["TeX Gyre Termes", "Times New Roman", "DejaVu Serif"],
                     "font.size": 8, "axes.spines.top": False, "axes.spines.right": False})
g = R.groupby("rate")[["shift_med", "shift_p90"]].median().sort_index(ascending=False)
fig, ax = plt.subplots(figsize=(3.2, 1.7))
ax.plot([50] + list(g.index), [0] + list(g.shift_med), "o-", color="#1b2430", ms=3.5, label="Median")
ax.plot([50] + list(g.index), [0] + list(g.shift_p90), "s--", color="#c2410c", ms=3.5, mfc="white", label="90th percentile")
ax.axhline(30, color="0.5", lw=0.8, ls=":"); ax.text(46, 33, "±30 ms strike window", ha="left", fontsize=6.5, color="0.35")
ax.set_xlabel("Command rate [Hz]"); ax.set_ylabel("Timing shift [ms]"); ax.set_ylim(0, 160); ax.invert_xaxis()
ax.legend(frameon=False, fontsize=6.5, loc="upper right"); ax.grid(alpha=0.25, lw=0.5); fig.tight_layout()
OUT = os.path.join(JP, "data", "user0", "jfps2026", "figures_paper"); os.makedirs(OUT, exist_ok=True)
for ext in ("pdf", "png"):
    fig.savefig(os.path.join(OUT, f"fig_rate.{ext}"), dpi=200)
