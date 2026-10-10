"""table1_models.py — 前刷 Table 1：既存モデルの検証（圧力の NRMSE）を最終の同定値で計算する

  モデル（DF チャネル。どれも compare_pressure_models.py と同じ実装）
    T : 従来のシミュレータ（遷移依存の時定数・むだ時間テーブル、方向反転で始点圧をラッチ）
    S : むだ時間＋一次遅れ
    O : S に給気・排気の流量上限（ISO 6358 型）を足したもの
  S・O のパラメータは struct_vs_data_fits.json の "step"（エコー付きステップ 4 本で同定。shaped / orificeV2 の土台と同じ）

  データ
    ① 同定に使ったステップ（エコーの指令、通信なし）：echo_grid, echo_steps×2, echo_bigstep
    ② 2月の検証データ（時間の伸びを補正）：準静的ヒステリシス、ステップ、チャープ
    ③ 打撃中の実機ログ 105 本（RA-L、ral_20260803）。DF/F/G の3チャネルとも DF の値で回す
  ②③ はエコーが無く通信の遅れを含むので、一定の遅れ δ（0〜80 ms）をモデルごとに1つだけ許す（全データ共通）

使い方: python analysis/itv/table1_models.py [<jetson_project>]
出力:   analysis/itv/table1_models.csv
"""
import os, sys, json, numpy as np, pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
JP = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("JETSON_PROJECT", os.path.abspath(os.path.join(_HERE, "..", "..")))
os.chdir(JP); sys.path.insert(0, "analysis")
# モデルとデータの読み込みは compare_pressure_models.py のものをそのまま使う（同定の部分は実行しない）
src = open("analysis/compare_pressure_models.py").read().split("# ------------------------------------------------------------------ fit on")[0]
exec(src.replace("cache=True", "cache=False"))

FITS = json.load(open(os.path.join(_HERE, "struct_vs_data_fits.json")))["step"]
TH = {"T": None, "S": np.array(FITS["S"]), "O": np.array(FITS["O"])}
NAMES = {"T": "従来（テーブル）", "S": "むだ時間＋一次遅れ", "O": "＋流量上限"}


def nrmse(p, m):
    return float(np.sqrt(np.mean((p - m) ** 2)) / (np.ptp(m) + 1e-12) * 100)


def load_step():
    out = []
    for f in ("data_echo_grid_1790668422.csv", "data_echo_steps_1790667361.csv",
              "data_echo_steps_1790666997.csv", "data_echo_bigstep_1790749923.csv"):
        d = pd.read_csv(find_measured(f))
        u = np.nan_to_num(d.flag.values.astype(float)); bad = np.abs(u) > 1
        if bad.any():
            u[bad] = np.interp(np.flatnonzero(bad), np.flatnonzero(~bad), u[~bad])
        i0 = int(np.argmax(u > 0.05))
        out.append((f, u[i0:], d.meas_pres_F.values[i0:]))     # 物理 DF = meas_pres_F
    return out


def best_shift(preds):
    """全データ共通の遅れ δ（0〜80 ms）を1つ選ぶ"""
    errs = [np.mean([np.mean((shift(p, s) - m) ** 2) for p, m in preds]) for s in range(17)]
    return int(np.argmin(errs))


rows = []
# ① 同定に使ったステップ
step = load_step()
r = {"データ": "① ステップ（同定に使用・4本の平均）"}
for k in "TSO":
    r[k] = np.mean([nrmse(run(k, TH[k], u), m) for _, u, m in step]); r[f"δ{k}"] = 0
rows.append(r)

# ② 2月の検証データ
feb = load_feb()
lab = {"exp1_static_hysteresis": "② 準静的ヒステリシス", "exp2_step_response": "② ステップ", "exp3_frequency_sweep": "② チャープ"}
res = {}
for k in "TSO":
    preds = [(run(k, TH[k], u), m) for _, u, m in feb]
    s = best_shift(preds)
    res[k] = (s * 5, [nrmse(shift(p, s), m) for p, m in preds])
for i, (e, _, _) in enumerate(feb):
    rows.append({"データ": lab[e], **{k: res[k][1][i] for k in "TSO"}, **{f"δ{k}": res[k][0] for k in "TSO"}})

# ③ 打撃中の 105 本（チャネルごとの中央値）
ral = load_ral()
tags = np.array([c for c, _, _ in ral])
res = {}
for k in "TSO":
    preds = [(run(k, TH[k], u), m) for _, u, m in ral]
    s = best_shift(preds)
    res[k] = (s * 5, np.array([nrmse(shift(p, s), m) for p, m in preds]))
n_runs = len(ral) // 3
for ch in ("DF", "F", "G"):
    sel = tags == ch
    rows.append({"データ": f"③ 打撃 {n_runs}本 {ch}（中央値）",
                 **{k: float(np.median(res[k][1][sel])) for k in "TSO"}, **{f"δ{k}": res[k][0] for k in "TSO"}})
    rows.append({"データ": f"③ 打撃 {n_runs}本 {ch}（四分位 25–75%）",
                 **{k: "%.1f–%.1f" % tuple(np.percentile(res[k][1][sel], [25, 75])) for k in "TSO"}})

T = pd.DataFrame(rows).set_index("データ").rename(columns=NAMES)
pd.set_option("display.width", 200)
print("S:", np.round(TH["S"], 4), " O:", np.round(TH["O"], 4))
print(T.to_string(float_format=lambda x: f"{x:.1f}"))
T.to_csv(os.path.join(_HERE, "table1_models.csv"), float_format="%.2f")
