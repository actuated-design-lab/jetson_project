"""input_types.py — 入力の種類ごとの圧力誤差（DF チャネル、エコーの指令を入力）

  時間波形：NRMSE [%]（実測のレンジで割る）。小ステップ・2段ステップ（反転）・ランダムな段・階段・ランプ・テスト動作
  持続した正弦：周波数ごとの振幅比の誤差 |モデル − 実機|（振幅 20 kPa 以上、中心 0.30 MPa 付近の区間の平均）
  モデル：O（流量上限、struct_vs_data_fits.json "step"。O の同定はエコー付きステップ4本のみで、下の表の区間はどれも使っていない）、A（shaped、fit_A.json）、B＋漏れ（fit_BL_w0.7.json）

使い方: python analysis/itv/input_types.py
出力:   analysis/itv/input_types.csv, input_types_sine.csv
"""
import os, sys, json, numpy as np, pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import pmeval as E  # noqa: E402
from base_eval import sim_O  # noqa: E402
from model_A import shape_cmd  # noqa: E402
from model_BL import sim_BL  # noqa: E402

A = json.load(open(os.path.join(_HERE, "fit_A.json")))
BL = json.load(open(os.path.join(_HERE, "fit_BL_w0.7.json")))
O = np.array(A["O"]); fg, ag, G = np.array(A["fg"]), np.array(A["ag"]), np.array(A["G"])
thBL = [BL[k] for k in ["L", "ki", "kpp", "d", "km", "cin", "cout", "ps", "b", "km_dn", "kl"]]
SIMS = {"O（流量上限）": lambda u, p0: sim_O(u, p0, *O),
        "A（shaped）": lambda u, p0: sim_O(shape_cmd(u, A["tc"], A["W"], A["hk"], A["h0"], fg, ag, G), p0, *O),
        "B＋漏れ": lambda u, p0: sim_BL(u, p0, *thBL)}
LABEL = {"smallstep": "小ステップ（5〜80 kPa）", "rev": "2段ステップ（反転・継続）", "rand": "ランダムな段",
         "stairs": "階段（5・10 kPa 刻み）", "ramp": "ランプ", "tmC": "正弦（テスト動作）", "tmD": "拮抗（テスト動作）",
         "tmE2": "打撃指令の再生 s2", "tmE3": "打撃指令の再生 s3"}

D = E.load_all()
res = {k: E.evaluate(D, s) for k, s in SIMS.items()}
T = pd.DataFrame({k: {LABEL[d]: r["nrmse"][d] for d in LABEL} for k, r in res.items()})
T["A・B の同定に使用"] = ["○" if E.FILES[d][2] == "fit" else "" for d in LABEL]
print("時間波形の NRMSE [%]"); print(T.round(2).to_string())
T.to_csv(os.path.join(_HERE, "input_types.csv"), float_format="%.2f")

rows = []
for k, r in res.items():
    M = r["map"]; M = M[(M.a >= 0.02) & (M.c.between(0.25, 0.35)) & (M.data != "tmC")]
    for f, g in M.groupby("f"):
        rows.append(dict(model=k, f=f, n=len(g), real=g.real.mean(), model_ratio=g.model.mean(),
                         abs_err=(g.model - g.real).abs().mean()))
S = pd.DataFrame(rows).pivot(index="f", columns="model", values="abs_err")
S.insert(0, "実機の振幅比（平均）", pd.DataFrame(rows).groupby("f").real.first())
print("\n持続した正弦：振幅比の誤差 |モデル − 実機|（周波数ごとの平均）"); print(S.round(3).to_string())
S.to_csv(os.path.join(_HERE, "input_types_sine.csv"), float_format="%.3f")
