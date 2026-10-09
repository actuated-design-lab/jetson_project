"""構造（T/S/O）× 同定データ（ステップのみ / ステップ＋正弦）で、同定に使っていない動作の圧力誤差を比べる
使い方: python struct_vs_data.py [<jetson_project>]  （省略時はこのリポジトリ）   （DF チャネル、エコーの指令を入力）"""
import os, sys, glob, json, numpy as np, pandas as pd
_HERE = os.path.dirname(os.path.abspath(__file__))
JP = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("JETSON_PROJECT", os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))); os.chdir(JP); sys.path.insert(0, "analysis")
src = open("analysis/compare_pressure_models.py").read().split("# ------------------------------------------------------------------ fit on")[0]
exec(src.replace("cache=True", "cache=False"))
from scipy.optimize import least_squares

def load(f, th=0.05):
    d = pd.read_csv(f); u = np.nan_to_num(d.flag.values.astype(float)); bad = np.abs(u) > 1
    if bad.any(): u[bad] = np.interp(np.flatnonzero(bad), np.flatnonzero(~bad), u[~bad])
    i0 = np.argmax(u > th); return u[i0:], d.meas_pres_F.values[i0:]   # 物理DF = meas_pres_F

from repo_paths import glob_measured  # noqa: E402  （find_measured は compare_pressure_models から）
STEP = [find_measured("data_echo_grid_1790668422.csv"), find_measured("data_echo_steps_1790667361.csv"),
        find_measured("data_echo_steps_1790666997.csv"), find_measured("data_echo_bigstep_1790749923.csv")]
SINE = [find_measured("data_echo_sine_1790749662.csv")]
VAL = {"ランダムな段": find_measured("data_pm_rand_1791267082.csv"),
       "正弦（テスト動作）": glob_measured("data_tm_C_sine_*.csv")[0],
       "拮抗（テスト動作）": glob_measured("data_tm_D_antagonist_*.csv")[0],
       "打撃指令の再生 s2": find_measured("data_tm_E_dbl160_seed2_1791183686.csv"),
       "打撃指令の再生 s3": find_measured("data_tm_E_dbl160_seed3_1791183707.csv")}

def fit(files):
    E = [load(f) for f in files]
    def res(th, mdl): return np.concatenate([(run(mdl, th, u) - m)[::4] for u, m in E])
    S = least_squares(res, [0.08, 0.04], args=("S",), bounds=([0.01, 0], [0.3, 0.1])).x
    O = least_squares(res, [0.05, 0.04, 3, 3, 0.6, 0.3], args=("O",),
                      bounds=([0.005, 0, 0.1, 0.1, 0.45, 0.05], [0.3, 0.1, 50, 50, 0.9, 0.6])).x
    return {"S": S, "O": O}

F = {"step": fit(STEP), "step+sine": fit(STEP + SINE)}
for k, v in F.items():
    print(k, "S tau=%.3f L=%.3f" % tuple(v["S"]), "| O", np.round(v["O"], 3))
nr = lambda p, m: np.sqrt(np.mean((p - m) ** 2)) / np.ptp(m) * 100
rows = []
for name, f in VAL.items():
    u, m = load(f, 0.02)
    r = {"データ": name, "T（ステップ）": nr(run("T", None, u), m)}
    for dk, lab in (("step", "ステップ"), ("step+sine", "ステップ＋正弦")):
        for mdl in "SO":
            r[f"{mdl}（{lab}）"] = nr(run(mdl, F[dk][mdl], u), m)
    rows.append(r)
R = pd.DataFrame(rows).set_index("データ").round(1)
print(R.to_string())
R.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "struct_vs_data.csv"))
json.dump({k: {m: list(map(float, v)) for m, v in d.items()} for k, d in F.items()}, open(os.path.join(_HERE, "struct_vs_data_fits.json"), "w"))
