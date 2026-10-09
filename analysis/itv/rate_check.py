"""方策の指令（50 Hz）をより粗い周期でサンプルし直して保持したとき、圧力（流量上限モデル）がどれだけ変わるか
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
