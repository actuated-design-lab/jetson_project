"""
gen_supply_probe.py — 元圧（供給圧）を、G のチャネルを「元圧計の代わり」にして測る

圧力センサが足りないので、G に 0.60 MPa を出させ続け、その実測を元圧の目安にする。
ITV2050 は 0.9 MPa まで出せるので、元圧が 0.6 MPa を下回ると G の実測は元圧に張り付く
（G は保持しているだけで空気をほとんど使わない）。

  tm_E_gmd138_seed2_Gprobe / seed3_Gprobe : tm_E の DF・F の指令はそのまま、G だけ 0.60 固定
  pm_supply : DF・F を 0.05⇔0.60 の逆相方形波（2 Hz）で 60 s 振り続ける（空気を一番使う動き）。
              前後に 20 s ずつ休止を入れて、元圧が落ちていく速さと戻る速さを見る

使い方:
  python tools/gen_supply_probe.py
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

DT = 0.02
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TS = os.path.join(ROOT, "signals")
G_PROBE = 0.60


def save(name, df, f, g, tag):
    d = pd.DataFrame({"cmd_pressure_DF": df, "cmd_pressure_F": f, "cmd_pressure_G": g})
    d.insert(0, "time", np.round(np.arange(len(d)) * DT, 4))
    d.to_csv(os.path.join(TS, f"{name}.csv"), index=False)
    d.assign(segment=tag).to_csv(os.path.join(TS, f"{name}_annotated.csv"), index=False)
    print(f"  {name:28s} {len(d) * DT / 60:4.1f} 分")


def sync(n_rest=75, n_up=50):
    """DF の同期ステップ（エコーでの時刻合わせ用）"""
    df = np.r_[np.full(n_rest, 0.10), np.full(n_up, 0.30), np.full(n_rest, 0.10)]
    return df, ["rest"] * n_rest + ["sync_up"] * n_up + ["sync_down"] * n_rest


if __name__ == "__main__":
    print("元圧の目安を G で測る信号")
    for seed in ("seed2", "seed3"):
        src = pd.read_csv(os.path.join(TS, f"tm_E_gmd138_{seed}.csv"))
        s_df, s_tag = sync()
        n = len(src)
        df = np.r_[s_df, src.cmd_pressure_DF.values, s_df]
        f = np.r_[np.full(len(s_df), 0.10), src.cmd_pressure_F.values, np.full(len(s_df), 0.10)]
        g = np.full(len(df), G_PROBE)
        tag = s_tag + ["E"] * n + s_tag
        save(f"tm_E_gmd138_{seed}_Gprobe", df, f, g, tag)

    s_df, s_tag = sync()
    rest = int(20 / DT); run = int(60 / DT)
    t = np.arange(run) * DT
    sq = (np.sin(2 * np.pi * 2.0 * t) >= 0).astype(float)
    df = np.r_[s_df, np.full(rest, 0.10), 0.05 + 0.55 * sq, np.full(rest, 0.10), s_df]
    f = np.r_[np.full(len(s_df), 0.10), np.full(rest, 0.10), 0.05 + 0.55 * (1 - sq), np.full(rest, 0.10), np.full(len(s_df), 0.10)]
    g = np.full(len(df), G_PROBE)
    tag = s_tag + ["pre_rest"] * rest + ["square_2Hz"] * run + ["post_rest"] * rest + s_tag
    save("pm_supply", df, f, g, tag)
