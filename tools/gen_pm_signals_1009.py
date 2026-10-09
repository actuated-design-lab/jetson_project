"""
gen_pm_signals_1009.py — JFPS 圧力モデル再同定（しきい値）用の測定信号を作る（10/9 実験日）

狙い：振幅×周波数マップで見えた「速くて小さい指令が通らない」しきい値の構造を見分け、
      パイロット段（積分）＋主弁の不感帯 のモデルを時間領域で同定するためのデータ。

  pm_smallstep : 小ステップ 5/10/20/40/80 kPa（上げ・下げ）× 中心 0.15/0.30/0.45 × 3回
                 → 「動き出しまでの時間がステップの大きさで変わるか」（積分＋不感帯なら小さいほど遅い）
  pm_ramp      : ランプ 0.05/0.1/0.2/0.5/1/2 MPa/s × 中心 0.15/0.30/0.45（±0.08 MPa）× 2回
                 → 「追従し始めるまでの遅れが 1/傾き に比例するか」
  pm_sine2     : 正弦 1.5/2.5/4/5 Hz × 振幅 10–120 kPa（中心 0.30）＋ 4 Hz を中心 0.15/0.45 でも
                 → 振幅比・位相マップの穴埋め（9/30 は 1/2/3/6 Hz）
  pm_stairs    : 10 kPa 刻みの階段 0.10→0.50→0.10、5 kPa 刻み 0.25→0.35→0.25（各段 0.6 s）× 2回
                 → 小さい変化が積み重なったときの追従（ヒステリシス）
  pm_rand      : ランダムな保持時間（40–400 ms）と大きさの段の列（90 s, seed 固定）
                 → 同定に使わない時間領域の検証用
  pm_fg        : F と G で小ステップと 3 Hz 正弦（レギュレータの個体差の確認）。
                 位置合わせ用に、最初と最後に DF の同期ステップを入れる

すべて DF 以外は F=0.10, G=0.30 固定（pm_fg を除く）。9/29–30 のエコー付きデータと同じ条件。
各ファイルの最初と最後に DF の同期ステップ（0.10→0.30→0.10）を入れる（エコーでの時刻合わせ用）。

出力: signals/<name>.csv と <name>_annotated.csv（segment 列つき, 50 Hz）
      run_signal_playback.py でそのまま流せる。

使い方:
  python tools/gen_pm_signals_1009.py
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

DT = 0.02
REST = (0.10, 0.10, 0.30)          # DF, F, G
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "signals")


class Seq:
    def __init__(self):
        self.rows: list[tuple[float, float, float]] = []
        self.tags: list[str] = []

    def add(self, df, f, g, tag):
        n = max(np.size(df), np.size(f), np.size(g))
        df, f, g = (np.broadcast_to(np.asarray(x, float), (n,)) for x in (df, f, g))
        self.rows += list(zip(np.clip(df, 0, 0.6), np.clip(f, 0, 0.6), np.clip(g, 0, 0.6)))
        self.tags += [tag] * n

    def hold(self, sec, df=REST[0], f=REST[1], g=REST[2], tag="rest"):
        n = int(round(sec / DT))
        self.add(np.full(n, df), np.full(n, f), np.full(n, g), tag)

    def sync(self, tag):
        """DF の同期ステップ（エコーで時刻を合わせるため。F/G は休止値）"""
        self.hold(1.5, tag="rest")
        self.hold(1.0, df=0.30, tag=f"{tag}_up")
        self.hold(1.5, tag=f"{tag}_down")

    def save(self, name):
        d = pd.DataFrame(self.rows, columns=["cmd_pressure_DF", "cmd_pressure_F", "cmd_pressure_G"])
        d.insert(0, "time", np.round(np.arange(len(d)) * DT, 4))
        d.to_csv(os.path.join(OUT, f"{name}.csv"), index=False)
        d.assign(segment=self.tags).to_csv(os.path.join(OUT, f"{name}_annotated.csv"), index=False)
        print(f"  {name:14s} {len(d) * DT / 60:4.1f} 分  ({len(d)} 行)")
        return len(d) * DT


def ch(seq, which, v, tag):
    """which ∈ {DF, F, G} のチャネルだけ v（配列）にして、ほかは休止値"""
    df, f, g = REST
    if which == "DF":
        seq.add(v, f, g, tag)
    elif which == "F":
        seq.add(df, v, g, tag)
    else:
        seq.add(df, f, v, tag)


def const(v, sec):
    return np.full(int(round(sec / DT)), float(v))


def sine(c, a, f, sec=3.0, taper=0.5):
    n = int(round(sec / DT)); t = np.arange(n) * DT
    w = np.minimum(1.0, np.minimum(t, t[::-1]) / taper)
    return c + a * w * np.sin(2 * np.pi * f * t)


# ---------------------------------------------------------------- 各ファイル
def pm_smallstep(rng, which="DF", centers=(0.15, 0.30, 0.45), sizes=(0.005, 0.01, 0.02, 0.04, 0.08), reps=3):
    s = Seq(); s.sync("sync0")
    conds = [(c, d) for c in centers for d in sizes]
    for r in range(reps):
        for i in rng.permutation(len(conds)):
            c, d = conds[i]
            ch(s, which, const(c, 1.5), f"settle_c{c}")
            ch(s, which, const(c + d, 1.0), f"step_up_c{c}_d{d}_r{r}")      # c → c+d
            ch(s, which, const(c, 1.0), f"step_back_c{c}_d{d}_r{r}")        # c+d → c（下げ）
            ch(s, which, const(c - d, 1.0), f"step_dn_c{c}_d{d}_r{r}")      # c → c−d
            ch(s, which, const(c, 1.0), f"step_ret_c{c}_d{d}_r{r}")         # c−d → c（上げ）
    s.sync("sync1")
    return s


def pm_ramp(rng, centers=(0.15, 0.30, 0.45), span=0.08, slopes=(0.05, 0.1, 0.2, 0.5, 1.0, 2.0), reps=2):
    s = Seq(); s.sync("sync0")
    conds = [(c, k) for c in centers for k in slopes]
    for r in range(reps):
        for i in rng.permutation(len(conds)):
            c, k = conds[i]
            lo, hi = c - span, c + span
            n = max(1, int(round((hi - lo) / k / DT)))
            s.add(const(lo, 1.5), REST[1], REST[2], f"settle_c{c}")
            s.add(np.linspace(lo, hi, n + 1)[1:], REST[1], REST[2], f"ramp_up_c{c}_k{k}_r{r}")
            s.add(const(hi, 1.0), REST[1], REST[2], f"hold_hi_c{c}_k{k}_r{r}")
            s.add(np.linspace(hi, lo, n + 1)[1:], REST[1], REST[2], f"ramp_dn_c{c}_k{k}_r{r}")
            s.add(const(lo, 1.0), REST[1], REST[2], f"hold_lo_c{c}_k{k}_r{r}")
    s.sync("sync1")
    return s


def pm_sine2(rng):
    s = Seq(); s.sync("sync0")
    amps = (0.01, 0.02, 0.03, 0.04, 0.06, 0.08, 0.12)
    conds = [(0.30, a, f) for f in (1.5, 2.5, 4.0, 5.0) for a in amps]
    conds += [(c, a, 4.0) for c in (0.15, 0.45) for a in amps]
    for i in rng.permutation(len(conds)):
        c, a, f = conds[i]
        s.add(const(c, 1.0), REST[1], REST[2], f"settle_c{c}")
        s.add(sine(c, a, f), REST[1], REST[2], f"amp_c{c}_a{a}_f{f}")
    s.sync("sync1")
    return s


def pm_stairs(rng, reps=2):
    s = Seq(); s.sync("sync0")
    for r in range(reps):
        up = np.round(np.arange(0.10, 0.50 + 1e-9, 0.01), 3)
        for k, v in enumerate(np.r_[up, up[::-1][1:]]):
            s.add(const(v, 0.6), REST[1], REST[2], f"stair10_{'up' if k < len(up) else 'dn'}_{v:.2f}_r{r}")
        s.add(const(0.25, 1.5), REST[1], REST[2], "settle_c0.25")
        up = np.round(np.arange(0.25, 0.35 + 1e-9, 0.005), 3)
        for k, v in enumerate(np.r_[up, up[::-1][1:]]):
            s.add(const(v, 0.6), REST[1], REST[2], f"stair5_{'up' if k < len(up) else 'dn'}_{v:.3f}_r{r}")
        s.add(const(0.10, 1.5), REST[1], REST[2], "rest")
    s.sync("sync1")
    return s


def pm_rand(rng, sec=90.0, lo=0.08, hi=0.55):
    s = Seq(); s.sync("sync0")
    v = 0.30; t = 0.0
    while t < sec:
        hold = rng.uniform(0.04, 0.40)
        u = rng.random()
        if u < 0.55:   d = rng.choice([-1, 1]) * rng.uniform(0.005, 0.03)    # 小さい段（しきい値付近）
        elif u < 0.9:  d = rng.choice([-1, 1]) * rng.uniform(0.03, 0.10)
        else:          d = rng.choice([-1, 1]) * rng.uniform(0.10, 0.30)    # たまに大きい段
        v = float(np.clip(v + d, lo, hi))
        n = max(2, int(round(hold / DT)))
        s.add(np.full(n, v), REST[1], REST[2], "rand")
        t += n * DT
    s.sync("sync1")
    return s


def pm_fg(rng):
    s = Seq(); s.sync("sync0")
    for which in ("F", "G"):
        rest_v = REST[1] if which == "F" else REST[2]
        for r in range(2):
            for d in (0.01, 0.02, 0.04):
                ch(s, which, const(0.30, 1.5), f"{which}_settle_c0.3")
                ch(s, which, const(0.30 + d, 1.0), f"{which}_step_up_c0.3_d{d}_r{r}")
                ch(s, which, const(0.30, 1.0), f"{which}_step_back_c0.3_d{d}_r{r}")
                ch(s, which, const(0.30 - d, 1.0), f"{which}_step_dn_c0.3_d{d}_r{r}")
                ch(s, which, const(0.30, 1.0), f"{which}_step_ret_c0.3_d{d}_r{r}")
        for a in (0.02, 0.06, 0.15):
            ch(s, which, const(0.30, 1.0), f"{which}_settle_c0.3")
            ch(s, which, sine(0.30, a, 3.0), f"{which}_amp_c0.3_a{a}_f3.0")
        ch(s, which, const(rest_v, 2.0), "rest")
    s.sync("sync1")
    return s


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    rng = np.random.default_rng(1009)
    print("10/9 圧力モデル再同定用の信号（優先度順）")
    total = 0.0
    for name, fn in [("pm_smallstep", pm_smallstep), ("pm_ramp", pm_ramp), ("pm_sine2", pm_sine2),
                     ("pm_rand", pm_rand), ("pm_stairs", pm_stairs), ("pm_fg", pm_fg)]:
        total += fn(rng).save(name)
    print(f"  合計 {total / 60:.1f} 分（間の待ちを除く）")
