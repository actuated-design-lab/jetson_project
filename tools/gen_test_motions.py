"""
gen_test_motions.py — JFPS 柱③「シミュレータで実機の動作を再現できるか」のテスト動作を作る

モデルの同定に使ったデータ（9/29・9/30 のエコー付きステップ／正弦／振幅掃引、
いずれも DF だけを振り F=0.10 固定）とは**条件を変えて**作る。
同じ信号で作って同じ信号で確かめると合って当たり前になるため。

  tm_A_quasistatic : DF をゆっくり往復（0.05 Hz 三角波、2往復）＋ F で同じこと
  tm_B_steps       : 上げ・下げの大小いろいろ（DF と F、同定に使っていない水準）
  tm_C_sine        : 正弦 1.5 / 2.5 / 4 Hz × 振幅 0.04 / 0.12 × 中心 0.25 / 0.40
                     （同定は 1/2/3/5/6/8 Hz・中心 0.15/0.30/0.45 なので重ならない）
  tm_D_antagonist  : DF と F を逆相で振る（1 / 2 / 3 Hz）、同相で上げる（共収縮）、
                     グリップを振る
  tm_E_<song>_<seed> : RA-L 実機ラン（data/ral_20260803）の方策の指令列をそのまま開ループで流す

出力: test_signals/<name>.csv と <name>_annotated.csv（segment 列つき）
      run_signal_playback.py と、シミュレータ側の replay_open_loop.py の両方でそのまま使える。

安全: 指令は 0〜0.6 MPa（方策が普段使う範囲）。両筋 0.35 MPa 以上の共収縮は 3 s 以内。

使い方:
  python tools/gen_test_motions.py
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

DT = 0.02
REST = (0.10, 0.10, 0.30)          # DF, F, G
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "test_signals")


class Seq:
    def __init__(self):
        self.rows: list[tuple[float, float, float]] = []
        self.tags: list[str] = []

    def add(self, df, f, g, tag):
        """各チャネルはスカラーでも配列でもよい（長さは最長のものに合わせる）"""
        n = max(np.size(df), np.size(f), np.size(g))
        df, f, g = (np.broadcast_to(np.asarray(x, float), (n,)) for x in (df, f, g))
        self.rows += list(zip(np.clip(df, 0, 0.6), np.clip(f, 0, 0.6), np.clip(g, 0, 0.6)))
        self.tags += [tag] * n

    def hold(self, sec, df=REST[0], f=REST[1], g=REST[2], tag="rest"):
        n = int(round(sec / DT))
        self.add(np.full(n, df), np.full(n, f), np.full(n, g), tag)

    def ramp_to(self, sec, target, tag="ramp"):
        n = int(round(sec / DT))
        a = np.array(self.rows[-1] if self.rows else REST)
        b = np.array(target)
        w = np.linspace(0, 1, n + 1)[1:, None]
        v = a + (b - a) * w
        self.add(v[:, 0], v[:, 1], v[:, 2], tag)

    def save(self, name):
        d = pd.DataFrame(self.rows, columns=["cmd_pressure_DF", "cmd_pressure_F", "cmd_pressure_G"])
        d.insert(0, "time", np.arange(len(d)) * DT)
        os.makedirs(OUT, exist_ok=True)
        d.to_csv(os.path.join(OUT, f"{name}.csv"), index=False)
        d.assign(segment=self.tags).to_csv(os.path.join(OUT, f"{name}_annotated.csv"), index=False)
        print(f"  {name:28s} {len(d) * DT:6.1f} s")


def taper(n, ramp_s=0.5):
    t = np.arange(n) * DT
    return np.minimum(1, np.minimum(t, t[::-1]) / ramp_s)


def motion_A():
    s = Seq(); s.hold(2.0)
    # DF: 0.05 Hz 三角波 0.05→0.55→0.05 を2往復（F は 0.10 固定）
    for ch in ("DF", "F"):
        for k in range(2):
            up = np.linspace(0.05, 0.55, int(10 / DT)); dn = up[::-1]
            v = np.r_[up, dn]
            if ch == "DF":
                s.add(v, REST[1], REST[2], f"A_{ch}_tri_{k}")
            else:
                s.add(REST[0], v, REST[2], f"A_{ch}_tri_{k}")
        s.ramp_to(1.0, REST); s.hold(3.0)
    s.save("tm_A_quasistatic")


def motion_B():
    s = Seq(); s.hold(2.0)
    # 同定に使っていない水準: 0.05 / 0.25 / 0.35 / 0.45 / 0.58
    for ch in ("DF", "F"):
        for a, b in [(0.05, 0.35), (0.35, 0.05), (0.05, 0.58), (0.58, 0.05), (0.25, 0.45),
                     (0.45, 0.25), (0.25, 0.58), (0.58, 0.25), (0.05, 0.25), (0.25, 0.05)]:
            pre = list(REST); post = list(REST)
            i = 0 if ch == "DF" else 1
            pre[i], post[i] = a, b
            s.hold(1.5, *pre, tag=f"B_{ch}_pre{a}")
            s.hold(2.0, *post, tag=f"B_{ch}_{a}to{b}")
        s.hold(2.0)
    s.save("tm_B_steps")


def motion_C():
    s = Seq(); s.hold(2.0)
    rng = np.random.default_rng(3)
    conds = [(c, a, f) for c in (0.25, 0.40) for a in (0.04, 0.12) for f in (1.5, 2.5, 4.0)]
    for i in rng.permutation(len(conds)):
        c, a, f = conds[i]
        n = int(5 / DT); t = np.arange(n) * DT
        s.hold(1.0, c, REST[1], REST[2], tag=f"settle_c{c}")
        s.add(c + a * taper(n) * np.sin(2 * np.pi * f * t), REST[1], REST[2], f"sine_c{c}_a{a}_f{f}")
        s.hold(1.5)
    s.save("tm_C_sine")


def motion_D():
    s = Seq(); s.hold(2.0)
    # 逆相（拮抗）: DF = 0.3 + a sin, F = 0.3 - a sin
    for f in (1.0, 2.0, 3.0):
        n = int(5 / DT); t = np.arange(n) * DT; w = taper(n)
        s.ramp_to(1.0, (0.30, 0.30, REST[2]), tag="to_mid")
        x = 0.12 * w * np.sin(2 * np.pi * f * t)
        s.add(0.30 + x, 0.30 - x, REST[2], f"anti_f{f}")
        s.ramp_to(1.0, REST); s.hold(1.5)
    # 同相（共収縮）: 両方 0.1→0.45→0.1 を 3 s ずつ
    for top in (0.30, 0.45):
        s.ramp_to(1.5, (top, top, REST[2]), tag=f"cocon_up{top}")
        s.hold(2.0, top, top, REST[2], tag=f"cocon_hold{top}")
        s.ramp_to(1.5, REST, tag=f"cocon_dn{top}"); s.hold(2.0)
    # 共収縮したまま DF を小さく振る（剛性が上がると角度の振れがどう変わるか）
    for base in (0.15, 0.35):
        n = int(4 / DT); t = np.arange(n) * DT
        s.ramp_to(1.0, (base, base, REST[2]), tag="to_base")
        s.add(base + 0.08 * taper(n) * np.sin(2 * np.pi * 2.0 * t), base, REST[2], f"stiff_base{base}_f2")
        s.ramp_to(1.0, REST); s.hold(1.5)
    # グリップを振る
    for f in (1.0, 3.0):
        n = int(4 / DT); t = np.arange(n) * DT
        s.add(REST[0], REST[1], 0.30 + 0.12 * taper(n) * np.sin(2 * np.pi * f * t), f"grip_f{f}")
        s.hold(1.5)
    s.save("tm_D_antagonist")


def motion_E():
    """RA-L 実機ランの方策の指令列を 50 Hz で取り出す（入れ替わりは指令側には無い）"""
    pick = [("test_double_bpm160", "RAL-E_seed2"), ("test_double_bpm160", "RAL-E_seed3"),
            ("gmd_03_high_bpm138", "RAL-E_seed2"), ("gmd_03_high_bpm138", "RAL-E_seed3")]
    for song, key in pick:
        fs = sorted(glob.glob(os.path.join(ROOT, "data", "ral_20260803", f"deploy_{song}_{key}_trial01_*.csv")))
        if not fs:
            print(f"  [skip] {song} {key}: ログが見つかりません"); continue
        d = pd.read_csv(fs[0])
        grid = np.arange(0, d["time"].iloc[-1], DT)
        idx = np.searchsorted(d["time"].values, grid)
        idx = np.clip(idx, 0, len(d) - 1)
        cmd = d[["cmd_DF", "cmd_F", "cmd_G"]].values[idx]
        cmd = np.nan_to_num(cmd, nan=0.0)
        s = Seq(); s.hold(2.0)
        s.ramp_to(0.5, tuple(cmd[0]), tag="to_start")
        s.add(cmd[:, 0], cmd[:, 1], cmd[:, 2], f"E_{song}_{key}")
        s.ramp_to(0.5, REST); s.hold(2.0)
        short = "dbl160" if "double" in song else "gmd138"
        s.save(f"tm_E_{short}_{key.split('_')[-1]}")


if __name__ == "__main__":
    print("テスト動作を生成:")
    motion_A(); motion_B(); motion_C(); motion_D(); motion_E()
