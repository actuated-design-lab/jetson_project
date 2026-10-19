"""isotonic.py — PAM 単体の等張性試験（おもり一定で長さを測る）

おもりの付け替えは人がやるので、付け替えの回数が少ない順に組んである。
  load_major（既定）    : おもりを 1 段替える → 圧力を 0→0.6→0 MPa の階段で自動で上げ下げ → 次のおもり …
                          付け替えはおもりの段数だけ。圧力の上げ下げのヒステリシスも同時に取れる
  pressure_major        : 圧力を固定したまま、おもりを 1 段ずつ足して外す（長さを変えたときのヒステリシス）
                          付け替えが多いので、圧力は 2〜3 本に絞る

付け替えの間は自動の操作を止めて Enter を待つ（このあいだ非常停止キーは効かない。Ctrl+C と自動監視は効く）。
自動で動いている間はスペース / e / q / Esc で即 0 MPa（非常停止）。
おもりの段ごとに途中保存するので、止まってもそこまでのデータは残る。

  export PORCARO_USER=user0 PORCARO_VENUE=pamtest
  python3 tools/pamtest/isotonic.py --pam DF_robot                       # load_major（plans/pamtest_isotonic.yaml）
  python3 tools/pamtest/isotonic.py --pam DF_robot --mode pressure_major
  python3 tools/pamtest/isotonic.py --pam DF_robot --mock                 # 実機なしのドライラン

出力: data/<user>/<venue>/isotonic_<日付>/isotonic_<pam>_<mode>_<unixtime>.csv / _steps.csv / .json
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

G = 9.80665


class Quit(Exception):
    """付け替え待ちで q：非常停止ではなく、0 MPa に戻して普通に終わる。"""


def ask(rig: Rig, msg: str, default: float | None = None) -> float | None:
    """付け替えの合図を待つ。Enter → default、数値 → その値、s → この段を飛ばす(None)。"""
    rig.keys.disarm()
    while True:
        try:
            s = input(f"\n>>> {msg} ").strip()
        except EOFError:
            s = ""
        if rig.stopped:
            raise EStop(rig.estop_reason or "estop")
        if s == "":
            return default
        if s.lower() == "s":
            return None
        if s.lower() == "q":
            raise Quit
        try:
            return float(s)
        except ValueError:
            print("    Enter / 数値 / s（飛ばす） / q（終わる） のどれかで")


def loads_sequence(plan: dict) -> list[tuple[float, str]]:
    loads = [float(x) for x in plan["loads_kg"]]
    seq = [(w, "add") for w in loads]
    if plan.get("return", True):
        seq += [(w, "remove") for w in reversed(loads[:-1])]
    return seq


class Session:
    def __init__(self, rig: Rig, plan: dict, args):
        self.rig, self.plan, self.args = rig, plan, args
        self.L0 = rig.cfg["pam"]["L0_mm"]
        self.hanger = float(plan.get("hanger_kg", 0.0))
        self.steps: list[dict] = []
        self.k = 0
        self.t0 = time.perf_counter()

    def contraction(self, mm: float) -> float:
        return self.rig.calib["sign"] * (mm - self.rig.laser_ref_mm) / self.L0 * 100.0

    def set_load(self, kg: float):
        if self.rig.mock:
            self.rig.ser.load_N = (kg + self.hanger) * G

    def reference(self):
        r = self.rig
        ask(r, f"0 MPa・吊り具だけ（{self.hanger} kg、おもり無し）にして Enter → 長さの基準を取る")
        self.set_load(0.0)
        r.keys.arm()
        r.set_tag(phase="reference", load_kg=0.0)
        r.wait(2.0)
        m = r.mean(1.5)
        r.laser_ref_mm = m["laser_mm"]
        r.event("reference", **m)
        print(f"\n  基準 {m['laser_mm']:.3f} mm（力 {m['force_N']:.2f} N）")

    def record(self, load_kg, load_dir, p_cmd, p_dir):
        h = self.plan["hold_s"]
        self.rig.wait(h)
        m = self.rig.mean(self.plan["avg_s"])
        self.k += 1
        row = {"step": self.k, "mode": self.args.mode, "load_kg": load_kg,
               "total_load_N": (load_kg + self.hanger) * G, "load_dir": load_dir,
               "P_cmd": p_cmd, "P_dir": p_dir, "P_DF": m["P_DF"], "laser_mm": m["laser_mm"],
               "laser_mm_std": m["laser_mm_std"], "contraction_pct": self.contraction(m["laser_mm"]),
               "force_N": m["force_N"], "force_N_std": m["force_N_std"],
               "t_rel": time.perf_counter() - self.t0}
        self.steps.append(row)
        print(f"\r  [{self.k:3d}] おもり {load_kg:5.2f} kg ({load_dir:6s}) P {p_cmd:.2f}→{m['P_DF']:.3f} MPa"
              f" ({p_dir:4s}) | 収縮 {row['contraction_pct']:6.2f} % | 力 {m['force_N']:7.2f} N"
              + " " * 20)

    # ---- おもり 1 段ごとに圧力の階段 ------------------------------------
    def run_load_major(self, save):
        r, pl = self.rig, self.plan
        rest = float(pl.get("rest_pressure", 0.0))
        seq = loads_sequence(pl)
        stairs = staircase(pl["pressures"], pl.get("pressure_updown", True))
        for i, (w, wdir) in enumerate(seq, 1):
            r.ramp_to(rest, pl["ramp_s"])
            got = ask(r, f"[{i}/{len(seq)}] {rest} MPa です。おもりを {w} kg にして Enter"
                         "（実際の値が違えば数値、s で飛ばす）", default=w)
            if got is None:
                continue
            w = got
            self.set_load(w)
            r.keys.arm()
            r.set_tag(phase="settle", load_kg=w, load_dir=wdir)
            r.wait(pl["settle_s"])
            for p, pdir in stairs:
                r.set_tag(phase="ramp", load_kg=w, load_dir=wdir, P_set=p, P_dir=pdir)
                r.ramp_to(p, pl["ramp_s"])
                r.set_tag(phase="hold", load_kg=w, load_dir=wdir, P_set=p, P_dir=pdir, step=self.k + 1)
                self.record(w, wdir, p, pdir)
            save()
        r.ramp_to(0.0, pl["ramp_s"])

    # ---- 圧力を固定して、おもりを 1 段ずつ -------------------------------
    def run_pressure_major(self, save):
        r, pl = self.rig, self.plan
        pm = pl["pressure_major"]
        seq = [(float(w), d) for w, d in loads_sequence({**pl, "loads_kg": pm.get("loads_kg", pl["loads_kg"])})]
        for P in pm["pressures"]:
            r.ramp_to(0.0, pl["ramp_s"])
            w0, d0 = seq[0]
            got = ask(r, f"=== {P} MPa の系列 === 0 MPa です。おもりを {w0} kg にして Enter", default=w0)
            w0 = w0 if got is None else got
            self.set_load(w0)
            r.keys.arm()
            r.set_tag(phase="ramp", load_kg=w0, load_dir=d0, P_set=P, P_dir="hold")
            r.ramp_to(P, pl["ramp_s"])
            r.wait(pl["settle_s"])
            r.set_tag(phase="hold", load_kg=w0, load_dir=d0, P_set=P, P_dir="hold", step=self.k + 1)
            self.record(w0, d0, P, "hold")
            for w, wdir in seq[1:]:
                got = ask(r, f"{P} MPa のまま（加圧中・手に注意）。おもりを {w} kg にして Enter", default=w)
                if got is None:
                    continue
                w = got
                self.set_load(w)
                r.keys.arm()
                r.set_tag(phase="settle", load_kg=w, load_dir=wdir, P_set=P, P_dir="hold")
                r.wait(pl["settle_s"])
                r.set_tag(phase="hold", load_kg=w, load_dir=wdir, P_set=P, P_dir="hold", step=self.k + 1)
                self.record(w, wdir, P, "hold")
            save()
        r.ramp_to(0.0, pl["ramp_s"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pam", required=True, help="試験する筋の名前（例: DF_robot, F_robot, spare1）。ファイル名に入る")
    ap.add_argument("--mode", choices=["load_major", "pressure_major"], default="load_major")
    ap.add_argument("--plan", default="tools/plans/pamtest_isotonic.yaml")
    ap.add_argument("--note", default="", help="メモ（json に残る）")
    ap.add_argument("--mock", action="store_true", help="実機なしのドライラン")
    add_user_venue_args(ap)
    a = ap.parse_args()
    user, venue = require_user_venue(a.user, a.venue)
    cfg, calib, plan = load_config(), load_calib(), load_plan(a.plan)
    if not calib["calibrated"]:
        print("[注意] laser_calib.json が無いので変位は生の値（mm ではない）。先に check.py で cal を推奨")
    if a.mock:
        plan = {**plan, "hold_s": 0.6, "avg_s": 0.3, "settle_s": 0.3, "ramp_s": 0.2}

    stem = f"isotonic_{a.pam}_{a.mode}_{int(time.time())}"
    out_dir = run_dir(user, venue, "isotonic")
    meta = {"test": "isotonic", "mode": a.mode, "pam": a.pam, "plan": plan, "plan_path": a.plan,
            "note": a.note, "mock": a.mock}
    print(f"[isotonic] {a.mode} / PAM {a.pam} / 保存先 {os.path.relpath(out_dir)}/{stem}.*")
    print("  自動で動いている間：スペース / e / q / Esc で非常停止（0 MPa）")

    rig = Rig(cfg, calib, mock=a.mock, isometric=False, use_force=False)   # 等張性はロードセルを使わない
    rig.start()
    sess = Session(rig, plan, a)
    save = lambda: save_run(rig, out_dir, stem, meta, sess.steps)  # noqa: E731
    try:
        sess.reference()
        (sess.run_load_major if a.mode == "load_major" else sess.run_pressure_major)(save)
        print("\n[isotonic] 終了")
    except Quit:
        rig.event("quit")
        rig.ramp_to(0.0, plan["ramp_s"])
        print("\n[isotonic] 途中で終了（q）")
    except EStop as e:
        print(f"\n[isotonic] 非常停止で終了: {e.reason}")
    except KeyboardInterrupt:
        rig.trigger_estop("Ctrl+C")
    finally:
        rig.close()
        base = save()
        print(f"[Saved] {os.path.relpath(base)}.csv / _steps.csv / .json（{len(sess.steps)} 段）")


if __name__ == "__main__":
    main()
