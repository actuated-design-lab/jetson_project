"""check.py — 試験機の配線確認とレーザ変位計の校正（試験の前に 1 回）

コマンド（1 行ずつ入力）
  m [秒]     各チャンネルを表示（既定 5 秒）。内圧・レーザ（生の値と mm）・力・flag・受信率
  p <MPa>    DF に圧力をかける（rig_config.yaml の p_cmd_max まで）。0 で抜く
  z          直近 2 秒の平均を表示（ロードセルの 0 点、レーザのばらつきの確認）
  cal        レーザの 2 点校正：ターゲットを 2 か所に置いて、それぞれの位置 [mm] を入力 → laser_calib.json
  dir        収縮の向き：0 MPa と 0.2 MPa（軽い荷重で）の変位を比べ、縮むと + になるように sign を決める
  q          0 MPa にして終わる

  python3 tools/pamtest/check.py            # PORCARO_USER / PORCARO_VENUE を export 済み
  python3 tools/pamtest/check.py --mock

記録: data/<user>/<venue>/rigcheck_<日付>/rigcheck_<unixtime>.csv.gz（このセッションの 200 Hz 全部）
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rig import (EStop, Rig, load_calib, load_config, save_calib,  # noqa: E402
                 save_run)
from repo_paths import add_user_venue_args, require_user_venue, run_dir  # noqa: E402


def monitor(rig: Rig, sec: float):
    t_end = time.perf_counter() + sec
    n0, t0 = rig.rx.n_packets, time.perf_counter()
    while time.perf_counter() < t_end:
        rig.check_estop()
        s = rig.rx.get_latest()
        rate = (rig.rx.n_packets - n0) / max(time.perf_counter() - t0, 1e-3)
        sys.stdout.write(f"\r  指令 {rig.target:5.3f} | 内圧 {s[rig.p_key]:6.3f} MPa | "
                         f"レーザ raw {s['meas_pres_F']:9.4f} → {rig.laser_mm(s['meas_pres_F']):8.3f} mm | "
                         f"力 {s['force_N']:7.2f} N | flag {s['flag']:.0f} | {rate:5.0f} Hz   ")
        sys.stdout.flush()
        time.sleep(0.2)
    print()


def calibrate(rig: Rig):
    pts = []
    for k in (1, 2):
        s = input(f"  点{k}: ターゲットを置いて、その位置 [mm] を入力（ゲージブロック・ノギスで） > ").strip()
        try:
            x = float(s)
        except ValueError:
            print("  中止")
            return
        time.sleep(0.5)
        m = rig.mean(2.0)
        raw_std = rig.window(2.0).meas_pres_F.std()
        pts.append((m["laser_raw"], x))
        print(f"    raw = {m['laser_raw']:.5f}（ばらつき {raw_std:.5f}）")
    (r1, x1), (r2, x2) = pts
    if abs(r2 - r1) < 1e-9:
        print("  2 点の raw が同じです（レーザが読めていない？）。中止")
        return
    gain = (x2 - x1) / (r2 - r1)
    offset = x1 - gain * r1
    rig.calib.update(gain=gain, offset=offset, points=[{"raw": r, "mm": x} for r, x in pts])
    save_calib(rig.calib)
    print(f"  mm = {gain:.5f} * raw + {offset:.4f} を laser_calib.json に保存")


def direction(rig: Rig, p: float = 0.2):
    rig.set_pressure(0.0)
    rig.wait(2.0, status=False)
    a = rig.mean(1.0)["laser_mm"]
    rig.ramp_to(p, 1.0)
    rig.wait(3.0, status=False)
    b = rig.mean(1.0)["laser_mm"]
    rig.ramp_to(0.0, 1.0)
    d = b - a
    print(f"  0 MPa: {a:.3f} mm, {p} MPa: {b:.3f} mm（差 {d:+.3f} mm）")
    if abs(d) < 0.2:
        print("  差が小さすぎます（荷重が重い・レーザが当たっていない？）。sign は変えません")
        return
    rig.calib["sign"] = 1.0 if d > 0 else -1.0
    save_calib(rig.calib)
    print(f"  sign = {rig.calib['sign']:+.0f}（縮むと収縮率が + になる向き）を保存")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true")
    add_user_venue_args(ap)
    a = ap.parse_args()
    user, venue = require_user_venue(a.user, a.venue)
    cfg, calib = load_config(), load_calib()
    print(f"[check] レーザ校正: {'あり ' + str({k: calib[k] for k in ('gain', 'offset', 'sign')}) if calib['calibrated'] else 'なし'}")
    rig = Rig(cfg, calib, mock=a.mock)
    rig.start(wait_s=0.3)
    stem = f"rigcheck_{int(time.time())}"
    try:
        monitor(rig, 3.0)
        while True:
            s = input("check> ").strip().split()
            if not s:
                continue
            c = s[0].lower()
            try:
                if c == "q":
                    break
                elif c == "m":
                    monitor(rig, float(s[1]) if len(s) > 1 else 5.0)
                elif c == "p" and len(s) > 1:
                    p = float(s[1])
                    if not 0.0 <= p <= cfg["limits"]["p_cmd_max"]:
                        print(f"  0〜{cfg['limits']['p_cmd_max']} MPa で")
                        continue
                    rig.set_tag(phase="manual", P_set=p)
                    rig.ramp_to(p, 1.0)
                    monitor(rig, 3.0)
                elif c == "z":
                    m = rig.mean(2.0)
                    print("  " + " | ".join(f"{k} {v:.4f}" for k, v in m.items() if k != "n"))
                elif c == "cal":
                    calibrate(rig)
                elif c == "dir":
                    direction(rig)
                else:
                    print("  m [秒] / p <MPa> / z / cal / dir / q")
            except EStop:
                print("  非常停止が掛かったので終わります")
                break
    except KeyboardInterrupt:
        pass
    finally:
        rig.close()
        base = save_run(rig, run_dir(user, venue, "rigcheck"), stem, {"test": "rigcheck", "mock": a.mock}, [])
        print(f"[Saved] {os.path.relpath(base)}.csv.gz")


if __name__ == "__main__":
    main()
