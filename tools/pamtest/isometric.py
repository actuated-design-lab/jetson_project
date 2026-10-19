"""isometric.py — PAM 単体の等尺性試験（長さを固定して、圧力ごとの力を測り続ける）

治具で長さを 1 つ固定したら、あとは圧力の階段（0→0.6→0 MPa）を --cycles 回くり返して、各段の力を記録する。
長さを変えるときはいったん終わって、治具を付け替えてから別のランとして流す（--contraction_pct を変える）。

非常停止
  * スペース / e / q / Esc（どれでも）→ 即 0 MPa にして保存して終わる。--cycles 0（止めるまでくり返す）の終わり方もこれ
  * 自動で止まる条件（rig_config.yaml の limits）：内圧の上限、力の上限、受信の途絶、
    変位が基準から isometric_move_mm 以上動いた（治具のずれ・破損）
  * Ctrl+C も同じ

  export PORCARO_USER=user0 PORCARO_VENUE=pamtest
  python3 tools/pamtest/isometric.py --pam DF_robot --contraction_pct 0
  python3 tools/pamtest/isometric.py --pam DF_robot --contraction_pct 4 --cycles 0   # 止めるまで
  python3 tools/pamtest/isometric.py --pam DF_robot --contraction_pct 0 --mock

出力: data/<user>/<venue>/isometric_<日付>/isometric_<pam>_c<収縮率>_<unixtime>.csv / _steps.csv / .json
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rig import (EStop, Rig, load_calib, load_config, load_plan,  # noqa: E402
                 save_run, staircase)
from repo_paths import add_user_venue_args, require_user_venue, run_dir  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pam", required=True, help="試験する筋の名前（例: DF_robot, spare1）。ファイル名に入る")
    ap.add_argument("--contraction_pct", type=float, required=True,
                    help="治具で固定した収縮率 [%]（自然長からの縮み / L0）。ファイル名と記録に入る")
    ap.add_argument("--cycles", type=int, default=None, help="圧力の階段の往復回数（0 = 止めるまで）。既定は plan")
    ap.add_argument("--plan", default="tools/plans/pamtest_isometric.yaml")
    ap.add_argument("--note", default="")
    ap.add_argument("--mock", action="store_true", help="実機なしのドライラン")
    add_user_venue_args(ap)
    a = ap.parse_args()
    user, venue = require_user_venue(a.user, a.venue)
    cfg, calib, plan = load_config(), load_calib(), load_plan(a.plan)
    if a.mock:
        plan = {**plan, "hold_s": 0.5, "avg_s": 0.3, "ramp_s": 0.2, "zero_s": 0.5}
    cycles = plan["cycles"] if a.cycles is None else a.cycles
    stairs = staircase(plan["pressures"], plan.get("pressure_updown", True))

    stem = f"isometric_{a.pam}_c{a.contraction_pct:g}_{int(time.time())}"
    out_dir = run_dir(user, venue, "isometric")
    meta = {"test": "isometric", "pam": a.pam, "contraction_pct": a.contraction_pct, "cycles": cycles,
            "plan": plan, "plan_path": a.plan, "note": a.note, "mock": a.mock}
    lim = cfg["limits"]
    print(f"[isometric] PAM {a.pam} / 収縮 {a.contraction_pct:g} % / {'止めるまで' if cycles == 0 else f'{cycles} 往復'}"
          f" / 保存先 {os.path.relpath(out_dir)}/{stem}.*")
    print(f"  自動停止: 内圧 > {lim['p_meas_max']} MPa, 力 > {lim['force_max_N']} N, "
          f"受信途絶 > {lim['rx_timeout_s']} s, 変位 > ±{lim['isometric_move_mm']} mm")

    rig = Rig(cfg, calib, mock=a.mock, isometric=True)
    if a.mock:
        rig.ser.fixed_h = a.contraction_pct / 100.0
    rig.start()
    steps: list[dict] = []
    t0 = time.perf_counter()
    try:
        rig.keys.disarm()
        input(f"\n>>> 治具を収縮 {a.contraction_pct:g} % で固定して、0 MPa のまま Enter → 力と変位の基準を取る ")
        rig.keys.arm()
        print("  ここから自動。スペース / e / q / Esc で非常停止")
        rig.set_tag(phase="zero")
        rig.wait(plan["zero_s"])
        z = rig.mean(plan["zero_s"] * 0.8)
        rig.laser_ref_mm = z["laser_mm"]          # ここから変位の監視が効く
        rig.event("zero", **z)
        print(f"\n  基準：力 {z['force_N']:.2f} N、変位 {z['laser_mm']:.3f} mm")
        c = 0
        while cycles == 0 or c < cycles:
            c += 1
            for p, pdir in stairs:
                rig.set_tag(phase="ramp", cycle=c, P_set=p, P_dir=pdir)
                rig.ramp_to(p, plan["ramp_s"])
                rig.set_tag(phase="hold", cycle=c, P_set=p, P_dir=pdir, step=len(steps) + 1)
                rig.wait(plan["hold_s"])
                m = rig.mean(plan["avg_s"])
                row = {"step": len(steps) + 1, "cycle": c, "contraction_set_pct": a.contraction_pct,
                       "P_cmd": p, "P_dir": pdir, "P_DF": m["P_DF"],
                       "force_N": m["force_N"], "force_N_std": m["force_N_std"],
                       "force_N_zeroed": m["force_N"] - z["force_N"],
                       "laser_mm": m["laser_mm"], "move_mm": m["laser_mm"] - z["laser_mm"],
                       "t_rel": time.perf_counter() - t0}
                steps.append(row)
                print(f"\r  [{row['step']:3d}] 往復 {c} P {p:.2f}→{m['P_DF']:.3f} MPa ({pdir:4s}) | "
                      f"力 {row['force_N_zeroed']:7.2f} N（±{m['force_N_std']:.2f}）| 動き {row['move_mm']:+.2f} mm"
                      + " " * 20)
            save_run(rig, out_dir, stem, meta, steps)       # 往復ごとに途中保存
        rig.ramp_to(0.0, plan["ramp_s"])
        print("\n[isometric] 終了")
    except EStop as e:
        print(f"\n[isometric] 停止: {e.reason}")
    except KeyboardInterrupt:
        rig.trigger_estop("Ctrl+C")
    finally:
        rig.close()
        base = save_run(rig, out_dir, stem, meta, steps)
        print(f"[Saved] {os.path.relpath(base)}.csv / _steps.csv / .json（{len(steps)} 段）")


if __name__ == "__main__":
    main()
