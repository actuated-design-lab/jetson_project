"""
check_pm_data.py — 10/9 の測定直後に「データが使えるか」をその場で確かめる

  python tools/check_pm_data.py pm_smallstep          # 最新の data_pm_smallstep_*.csv（data/*/*/playback_*/）を見る
  python tools/check_pm_data.py pm_ramp --file data/user0/jfps2026/playback_20261009/data_pm_ramp_XXXX.csv

見るもの（どのファイルでも）:
  1. 長さ：ログの長さ / 信号の長さ（≈1.00。1.03 などなら再生の時間ずれ）
  2. 受信の途切れ：PC受信時刻の最大の空き（>50 ms が多いと要注意）
  3. エコー：flag が DF 指令のエコーになっているか（ずれ [ms] と一致度）
  4. 元圧：同期ステップ（DF 0.30）で実測が 0.25–0.35 MPa に入るか
ファイルごとの簡易結果:
  pm_smallstep / pm_fg : ステップの大きさごとの「50% に届くまでの時間」と「1 s 後の到達率」
  pm_ramp              : 傾きごとの遅れ（指令と実測が区間の中点を通る時刻の差）
  pm_sine2             : 中心 0.30 の振幅比（周波数×振幅）
  pm_stairs / pm_rand  : 基本チェックのみ
  pm_rev               : 2段ステップの2段目の応答（反転と継続の比較）
  pm_ch_F / pm_ch_G    : そのチャネルの小ステップ・振幅比・反転

注意：実機ログは圧力の列が入れ替わっている（2026/9/29 判明）。
      物理的な DF = meas_pres_F 列、物理的な F = meas_pres_DF 列、G はそのまま。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import numpy as np
import pandas as pd

DT = 0.005
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TS = os.path.join(ROOT, "signals")   # 入力信号
sys.path.insert(0, os.path.join(ROOT, "src"))
from repo_paths import glob_measured  # noqa: E402
PHYS = {"DF": "meas_pres_F", "F": "meas_pres_DF", "G": "meas_pres_G"}   # 入れ替わりを戻す


def latest(name):
    fs = sorted(glob_measured(f"data_{name}_*.csv"), key=os.path.getmtime)
    if not fs:
        sys.exit(f"[エラー] data/<user>/<venue>/playback_*/data_{name}_*.csv がありません")
    return fs[-1]


def echo_lag(flag, cmd50):
    """flag（200 Hz）と DF 指令（50 Hz）のずれを探す。戻り値: (lag[サンプル], 平均二乗誤差)"""
    n = len(flag)
    cmd = cmd50[np.clip((np.arange(n) * DT / 0.02).astype(int), 0, len(cmd50) - 1)]
    best = (1e9, 0)
    for lag in range(-100, 400):
        a, b = (cmd[: n - lag], flag[lag:]) if lag >= 0 else (cmd[-lag:], flag[: n + lag])
        m = min(len(a), len(b))
        if m < 200:
            continue
        e = float(np.mean((a[:m] - b[:m]) ** 2))
        if e < best[0]:
            best = (e, lag)
    return best[1], best[0]


def smooth(x, k=5):
    return np.convolve(x, np.ones(k) / k, mode="same")


def step_table(u, p, title, allow=None):
    """u: 受信した指令（エコー）, p: 実測。保持 ≥0.5 s のあとで変わった段を拾う。
    allow（真偽配列）を渡すと、その位置で始まる段だけを数える"""
    ps = smooth(p)
    du = np.diff(u); idx = np.flatnonzero(np.abs(du) > 0.003) + 1
    rows = []
    last = 0
    for i in idx:
        if i - last < int(0.5 / DT) or i + int(1.0 / DT) >= len(u) or (allow is not None and not allow[i]):
            last = i; continue
        d = u[i] - u[i - 1]
        p0 = np.median(p[i - int(0.2 / DT): i])
        seg = ps[i: i + int(1.0 / DT)]
        cross = np.flatnonzero((seg - p0) * np.sign(d) >= 0.5 * abs(d))
        t50 = cross[0] * DT * 1000 if len(cross) else np.nan
        reach = (np.median(p[i + int(0.8 / DT): i + int(0.95 / DT)]) - p0) / d
        rows.append((round(abs(d), 3), "上げ" if d > 0 else "下げ", t50, reach))
        last = i
    if not rows:
        print(f"  {title}: ステップが見つかりません"); return
    T = pd.DataFrame(rows, columns=["大きさ", "向き", "t50", "到達率"])
    print(f"\n  {title}：ステップの大きさごと（中央値、t50=50%に届くまで[ms]、到達率=1 s 後）")
    g = T.groupby(["大きさ", "向き"]).agg(回数=("t50", "size"), t50=("t50", "median"),
                                          届かない=("t50", lambda s: int(s.isna().sum())), 到達率=("到達率", "median"))
    print(g.round(2).to_string())


def rev_table(u, p, seg, title):
    """2段ステップ（pm_rev / pm_ch_*）：2段目に対して 200 ms 後までに動いた割合。
    線形なら『反転』と『継続』の平均は d, h によらず一定（1段目の残りが打ち消し合う）"""
    ps = smooth(p); rows = []
    for L in pd.unique(seg):
        mm = re.match(r"(rev|cont)_(up|dn)_c([\d.]+)_d([\d.]+)_h([\d.]+)_r\d_s2$", str(L))
        if not mm:
            continue
        ii = np.flatnonzero(seg == L)
        if len(ii) < 60 or ii[0] < 50:
            continue
        w = u[ii[0] - 20: ii[0] + 20]; k = np.flatnonzero(np.abs(np.diff(w)) > 0.003)
        on = ii[0] - 20 + k[-1] + 1 if len(k) else ii[0]
        dd = float(mm[4]); first = 1 if mm[2] == "up" else -1; second = -first if mm[1] == "rev" else first
        frac = (ps[on + int(0.2 / DT)] - ps[on]) / (second * dd)
        rows.append((mm[1], dd * 1000, float(mm[5]) * 1000, frac))
    if not rows:
        print(f"  {title}: 2段ステップが見つかりません"); return
    T = pd.DataFrame(rows, columns=["種類", "d[kPa]", "h[ms]", "割合"])
    piv = T.pivot_table(index=["d[kPa]", "h[ms]"], columns="種類", values="割合", aggfunc="median")
    if "cont" in piv:
        piv["平均"] = piv.mean(axis=1)
    print(f"\n  {title}：2段目に対して 200 ms 後までに動いた割合（中央値）")
    print("  rev=反転, cont=継続。線形なら『平均』は d・h によらずほぼ一定。小さい d・短い h で下がれば反転のしきい値")
    print(piv.round(2).to_string())


def lock(x, tt, f):
    c = np.cos(2 * np.pi * f * tt); s = np.sin(2 * np.pi * f * tt); x = x - x.mean()
    return np.hypot(2 * np.mean(x * c), 2 * np.mean(x * s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="信号名（例 pm_smallstep）")
    ap.add_argument("--file", default=None, help="ログを直接指定（省略時は最新）")
    a = ap.parse_args()
    lf = a.file or latest(a.name)
    sig = pd.read_csv(os.path.join(TS, f"{a.name}_annotated.csv"))
    d = pd.read_csv(lf)
    print(f"ログ: {os.path.relpath(lf, ROOT)}")

    # 1. 長さ
    ratio = len(d) * DT / (len(sig) * 0.02)
    print(f"  長さ  ログ/信号 = {ratio:.3f}   {'OK' if abs(ratio - 1) < 0.01 else '★ 要確認（再生の時間ずれ？）'}")
    # 2. 受信の途切れ
    gap = np.diff(d["timestamp_pc"].values) * 1000
    print(f"  受信の空き  最大 {gap.max():.0f} ms, >50 ms が {int((gap > 50).sum())} 回   "
          f"{'OK' if (gap > 50).sum() < 5 else '★ 要確認'}")
    # 3. エコー
    flag = np.nan_to_num(d["flag"].values.astype(float))
    bad = np.abs(flag) > 1.0                      # パケットの読み取りミス
    if bad.any():
        flag[bad] = np.interp(np.flatnonzero(bad), np.flatnonzero(~bad), flag[~bad])
        print(f"  （flag の異常値 {int(bad.sum())} 点を補間）")
    lag, err = echo_lag(flag, sig["cmd_pressure_DF"].values)
    ok_echo = err < 2e-3 and np.std(flag) > 0.01
    print(f"  エコー  ずれ {lag * DT * 1000:.0f} ms, 一致度(MSE) {err:.1e}   "
          f"{'OK' if ok_echo else '★ flag がエコーになっていない（ControlDesk を再読み込み）'}")
    # 4. 元圧（同期ステップ）
    pDF = d[PHYS["DF"]].values
    i = int(np.argmax(flag > 0.25))
    v = np.median(pDF[i + int(0.5 / DT): i + int(0.9 / DT)]) if i > 0 else np.nan
    print(f"  元圧  同期ステップ(0.30)で DF 実測 {v:.3f} MPa   {'OK' if 0.25 < v < 0.35 else '★ 元圧・配管を確認'}")

    # 信号の区間をログ時刻へ（ログ j ↔ 信号時刻 (j − lag)·DT）
    j = np.arange(len(d))
    seg = sig["segment"].values[np.clip(((j - lag) * DT / 0.02).astype(int), 0, len(sig) - 1)]

    if a.name == "pm_smallstep":
        step_table(flag, pDF, "DF", allow=np.array([str(x).startswith("step_") for x in seg]))
    elif a.name == "pm_rev":
        rev_table(flag, pDF, seg, "DF")
    elif a.name in ("pm_ch_F", "pm_ch_G"):
        w = a.name[-1]
        u = sig[f"cmd_pressure_{w}"].values[np.clip(((j - lag) * DT / 0.02).astype(int), 0, len(sig) - 1)]
        pw = d[PHYS[w]].values
        step_table(u, pw, w, allow=np.array([str(x).startswith("step_") for x in seg]))
        rows = []
        for L in pd.unique(seg):
            mm = re.match(r"amp_c([\d.]+)_a([\d.]+)_f([\d.]+)", str(L))
            if not mm:
                continue
            ii = np.flatnonzero(seg == L)[30:-30]; tt = ii * DT; f = float(mm[3])
            rows.append((float(mm[2]), f, lock(pw[ii], tt, f) / max(lock(u[ii], tt, f), 1e-6)))
        T = pd.DataFrame(rows, columns=["a", "f", "gain"])
        print(f"\n  {w} の振幅比（中心 0.30）：行=振幅[MPa], 列=周波数[Hz]")
        print(T.pivot_table(index="a", columns="f", values="gain").round(2).to_string())
        rev_table(u, pw, seg, w)
    elif a.name in ("pm_stairs", "pm_rand"):
        print("\n  （このファイルは基本チェックのみ。中身の解析は持ち帰ってから）")
    elif a.name == "pm_fg":
        # F と G はエコーが無いので、DF のエコーで求めたずれで指令列をずらして使う
        for w, col in (("F", "cmd_pressure_F"), ("G", "cmd_pressure_G")):
            u = sig[col].values[np.clip(((j - lag) * DT / 0.02).astype(int), 0, len(sig) - 1)]
            m = np.array([s.startswith(w + "_") for s in seg])
            first, lastk = np.flatnonzero(m)[[0, -1]]
            ok = np.array([str(x).startswith(w + "_step_") for x in seg])
            step_table(u[first:lastk], d[PHYS[w]].values[first:lastk], w, allow=ok[first:lastk])
    elif a.name == "pm_ramp":
        ps = smooth(pDF); rows = []
        for L in pd.unique(seg):
            mm = re.match(r"ramp_(up|dn)_c([\d.]+)_k([\d.]+)_r\d", str(L))
            if not mm:
                continue
            ii = np.flatnonzero(seg == L)
            if len(ii) < 2:
                continue
            u0, u1 = flag[ii[0] - 1], flag[ii[-1]]; mid = 0.5 * (u0 + u1); sgn = np.sign(u1 - u0)
            tu = np.flatnonzero((flag[ii[0]: ii[0] + 800] - mid) * sgn >= 0)
            tp = np.flatnonzero((ps[ii[0]: ii[0] + 800] - mid) * sgn >= 0)
            delay = (tp[0] - tu[0]) * DT * 1000 if len(tu) and len(tp) else np.nan
            rows.append((float(mm[3]), "上げ" if mm[1] == "up" else "下げ", float(mm[2]), delay))
        T = pd.DataFrame(rows, columns=["傾き[MPa/s]", "向き", "中心", "遅れ[ms]"])
        print("\n  ランプ：中点を通る時刻の差（中央値, ms）")
        print(T.pivot_table(index="傾き[MPa/s]", columns="向き", values="遅れ[ms]", aggfunc="median").round(0).to_string())
    elif a.name == "pm_sine2":
        rows = []
        for L in pd.unique(seg):
            mm = re.match(r"amp_c([\d.]+)_a([\d.]+)_f([\d.]+)", str(L))
            if not mm:
                continue
            ii = np.flatnonzero(seg == L)[30:-30]; tt = ii * DT; f = float(mm[3])
            rows.append((float(mm[1]), float(mm[2]), f, lock(pDF[ii], tt, f) / max(lock(flag[ii], tt, f), 1e-6)))
        T = pd.DataFrame(rows, columns=["c", "a", "f", "gain"])
        print("\n  振幅比（実測/指令）中心 0.30：行=振幅[MPa], 列=周波数[Hz]")
        print(T[T.c == 0.30].pivot_table(index="a", columns="f", values="gain").round(2).to_string())
        print("  ※ 振幅 0.01 は圧力のノイズ（±10 kPa 程度）に埋もれるので参考値")
    print("\n終わったら次の信号へ。全部終わったら chown → push。")


if __name__ == "__main__":
    main()
