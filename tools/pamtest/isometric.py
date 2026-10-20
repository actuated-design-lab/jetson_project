"""isometric.py — PAM 単体の等尺性試験（長さを固定して、圧力ごとの力を測る）。長さを何段か変えて 1 セッションで回す

PAM は手で縮む向きには動かせない（硬い）ので、長さを変えるときは圧力で筋を縮めてから治具を固定する。
1 セッションの流れ（長さは plan の contractions_pct の順。既定 0 → 2 → … → 14 %）:

  0. 自然長の基準   治具をゆるめて 0 MPa → Enter。レーザの位置を自然長として記録（全部の長さの基準）
  各長さ c について
  1. ゆるめる       [人] 0 MPa で治具をゆるめて Enter（c = 0 % はそのまま自然長で固定）
  2. 縮める         [自動] 圧力をゆっくり上げ、収縮率が c に届いたら止める
                    [人] 必要なら + / − で微調整 → 治具を固定して Enter（このあいだ加圧したまま。手に注意）
  3. 0 MPa に抜く   [自動] 治具が長さを保つ。0 MPa で力の 0 点と固定位置を取り、実際の収縮率を表示
  4. 測る           [自動] 圧力の階段 0→0.6→0 MPa を cycles 往復。各段の力を記録
  → 次の長さへ（1 に戻る）

非常停止
  * 自動で動いている間（2 の自動部分・3・4）はスペース / e / q / Esc で即 0 MPa（Ctrl+C も同じ）
  * 自動で止まる条件（rig_config.yaml の limits）：内圧の上限、力の上限、受信の途絶、
    4 の間に変位が固定位置から isometric_move_mm 以上動いた（治具のずれ・破損）
  * 人の操作待ち（Enter 待ち）の間はキーの非常停止は効かない（Ctrl+C と自動停止は効く）

途中で止まったら（MicroLabBox が止まった等）、自然長の基準を引き継いで、その長さから再開できる:
  python3 tools/pamtest/isometric.py --pam DF_robot --start_pct 6 --ref_from <前のセッションの .json>

  export PORCARO_USER=user0 PORCARO_VENUE=pamtest
  python3 tools/pamtest/isometric.py --pam DF_robot                              # plan の長さを全部
  python3 tools/pamtest/isometric.py --pam DF_robot --contractions 0 4 8         # 長さを指定
  python3 tools/pamtest/isometric.py --pam DF_robot --mock                       # 実機なしのドライラン

出力: data/<user>/<venue>/isometric_<日付>/isometric_<pam>_<unixtime>.csv.gz / _steps.csv / .json
      （1 セッション 1 組。長さごとに途中保存。_steps.csv に設定と実際の収縮率）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rig import (EStop, Rig, load_calib, load_config, load_plan,  # noqa: E402
                 save_run, staircase)
from repo_paths import add_user_venue_args, require_user_venue, run_dir  # noqa: E402


class Skip(Exception):
    """この長さを飛ばす。"""


class Session:
    def __init__(self, rig: Rig, plan: dict, args):
        self.rig, self.plan, self.args = rig, plan, args
        self.L0 = rig.cfg["pam"]["L0_mm"]
        self.steps: list[dict] = []
        self.lengths: list[dict] = []
        self.t0 = time.perf_counter()

    # ---- 共通 -----------------------------------------------------------------
    def contraction(self, mm: float) -> float:
        """自然長の基準からの収縮率 [%]（縮むと +）。"""
        return self.rig.calib["sign"] * (mm - self.rig.laser_ref_mm) / self.L0 * 100.0

    def now_c(self, sec: float = 0.3) -> float:
        return self.contraction(self.rig.mean(sec)["laser_mm"])

    def ask(self, msg: str) -> str:
        r = self.rig
        r.keys.disarm()
        try:
            s = input(f"\n>>> {msg} ").strip()
        except EOFError:
            s = ""
        if r.stopped:
            raise EStop(r.estop_reason or "estop")
        if s.lower() == "q":
            raise KeyboardInterrupt
        return s

    def mock_loose(self):
        if self.rig.mock:
            self.rig.ser.fixed_h, self.rig.ser.load_N = None, 0.5

    def mock_fix(self):
        if self.rig.mock:
            self.rig.ser.fixed_h = self.rig.ser.h

    # ---- 0. 自然長 ---------------------------------------------------------------
    def natural_reference(self):
        r = self.rig
        if self.args.ref_from:
            with open(self.args.ref_from, encoding="utf-8") as f:
                prev = json.load(f)
            ref = prev.get("natural_ref_mm")
            if ref is None:
                sys.exit(f"[isometric] {self.args.ref_from} に natural_ref_mm がありません")
            r.laser_ref_mm = float(ref)
            r.event("natural_reference_inherited", ref_from=self.args.ref_from, laser_mm=r.laser_ref_mm)
            print(f"  自然長の基準を引き継ぎ：{r.laser_ref_mm:.3f} mm（{os.path.basename(self.args.ref_from)}）")
            return
        self.mock_loose()
        self.ask("治具をゆるめて（筋が自由に動く状態）、0 MPa のまま Enter → 自然長の基準を取る")
        r.keys.arm()
        r.set_tag(phase="natural_ref")
        r.wait(2.0)
        m = r.mean(1.5)
        r.laser_ref_mm = m["laser_mm"]
        r.event("natural_reference", **m)
        print(f"\n  自然長の基準 {m['laser_mm']:.3f} mm")

    # ---- 1〜2. ゆるめて縮める ---------------------------------------------------
    def approach(self, c: float) -> float:
        """圧力をゆっくり上げて収縮率を c に近づけ、人が治具を固定するまで保つ。固定したときの圧力を返す。"""
        r, pl = self.rig, self.plan
        tol = pl["approach_tol_pct"]
        self.ask(f"0 MPa です。治具をゆるめて Enter（いま収縮 {self.now_c():.2f} %）")
        self.mock_loose()
        r.keys.arm()
        r.set_tag(phase="approach", c_set=c)
        rate = pl["approach_rate_MPa_s"]
        print(f"  {c:g} % まで縮めます（{rate} MPa/s で上げる。スペース等で非常停止）")
        p = r.target
        while self.now_c(0.1) < c - tol / 2:
            if p >= r.lim["p_cmd_max"]:
                print(f"\n  {r.lim['p_cmd_max']} MPa でも {c:g} % に届きません（いま {self.now_c():.2f} %）")
                break
            p = min(p + rate * 0.05, r.lim["p_cmd_max"])
            r.set_pressure(p)
            r.wait(0.05, status=False)
            r.print_status(f"収縮 {self.now_c(0.1):5.2f} % → 目標 {c:g} %")
        r.wait(pl["approach_settle_s"], status=False)
        step = pl["approach_nudge_MPa"]
        while True:
            s = self.ask(f"収縮 {self.now_c():.2f} %（目標 {c:g} %、{r.target:.3f} MPa）。治具を固定して Enter ／ "
                         f"+ / − で {step} MPa ずつ ／ 数値で圧力 [MPa] ／ s でこの長さを飛ばす")
            if s == "":
                break
            if s.lower() == "s":
                raise Skip
            try:
                p = r.target + step if s == "+" else r.target - step if s in ("-", "−") else float(s)
            except ValueError:
                print("    Enter / + / − / 数値 / s / q（終わる）")
                continue
            p = min(max(p, 0.0), r.lim["p_cmd_max"])
            r.keys.arm()
            r.ramp_to(p, 0.5)
            r.wait(pl["approach_settle_s"], status=False)
        self.mock_fix()
        r.event("fixed", c_set=c, P=r.target, c_now=self.now_c())
        return r.target

    # ---- 3〜4. 0 MPa に抜いて測る -----------------------------------------------
    def measure(self, c: float, p_fix: float | None):
        r, pl = self.rig, self.plan
        r.keys.arm()
        r.set_tag(phase="release", c_set=c)
        r.ramp_to(0.0, pl["ramp_s"])
        r.set_tag(phase="zero", c_set=c)
        r.wait(pl["zero_s"])
        z = r.mean(pl["zero_s"] * 0.8)
        c_act = self.contraction(z["laser_mm"])
        r.move_ref_mm = z["laser_mm"]                 # ここから変位の監視が効く
        r.event("zero", c_set=c, c_actual=c_act, **z)
        warn = "  ★設定とずれています" if abs(c_act - c) > pl["approach_tol_pct"] * 2 else ""
        print(f"\n  固定：実際の収縮 {c_act:.2f} %（設定 {c:g} %）、力の 0 点 {z['force_N']:.2f} N{warn}")
        self.lengths.append({"c_set": c, "c_actual": c_act, "P_fix": p_fix, "zero_force_N": z["force_N"],
                             "fixed_mm": z["laser_mm"]})
        stairs = staircase(pl["pressures"], pl.get("pressure_updown", True))
        cycles = pl["cycles"] if self.args.cycles is None else self.args.cycles
        for cyc in range(1, cycles + 1):
            for p, pdir in stairs:
                r.set_tag(phase="ramp", c_set=c, cycle=cyc, P_set=p, P_dir=pdir)
                r.ramp_to(p, pl["ramp_s"])
                r.set_tag(phase="hold", c_set=c, cycle=cyc, P_set=p, P_dir=pdir, step=len(self.steps) + 1)
                r.wait(pl["hold_s"])
                m = r.mean(pl["avg_s"])
                row = {"step": len(self.steps) + 1, "c_set": c, "c_actual": c_act, "cycle": cyc,
                       "P_cmd": p, "P_dir": pdir, "P_meas": m["P_meas"],
                       "force_N": m["force_N"], "force_N_std": m["force_N_std"],
                       "force_N_zeroed": m["force_N"] - z["force_N"],
                       "laser_mm": m["laser_mm"], "move_mm": m["laser_mm"] - z["laser_mm"],
                       "t_rel": time.perf_counter() - self.t0}
                self.steps.append(row)
                print(f"\r  [{row['step']:3d}] 収縮 {c:g} % 往復 {cyc} P {p:.2f}→{m['P_meas']:.3f} MPa ({pdir:4s}) | "
                      f"力 {row['force_N_zeroed']:7.2f} N（±{m['force_N_std']:.2f}）| 動き {row['move_mm']:+.2f} mm"
                      + " " * 10)
        r.ramp_to(0.0, pl["ramp_s"])
        r.move_ref_mm = None                          # 治具をゆるめるので監視を外す

    def run(self, contractions: list[float], save):
        self.natural_reference()
        save()
        for c in contractions:
            print(f"\n=== 収縮 {c:g} % ===")
            try:
                if c == 0:
                    self.mock_loose()
                    self.ask("0 MPa のまま、治具を自然長（収縮 0 %）で固定して Enter")
                    self.mock_fix()
                    p_fix = 0.0
                else:
                    p_fix = self.approach(c)
            except Skip:
                self.rig.ramp_to(0.0, self.plan["ramp_s"])
                self.rig.event("skip", c_set=c)
                print(f"  {c:g} % を飛ばしました")
                continue
            self.measure(c, p_fix)
            save()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pam", required=True, help="試験する筋の名前（例: DF_robot, spare1）。ファイル名に入る")
    ap.add_argument("--contractions", type=float, nargs="+", default=None,
                    help="測る収縮率 [%] の並び（既定は plan の contractions_pct）")
    ap.add_argument("--start_pct", type=float, default=None, help="この収縮率から始める（途中で止まったときの再開）")
    ap.add_argument("--ref_from", default=None, help="自然長の基準を引き継ぐ前のセッションの .json")
    ap.add_argument("--cycles", type=int, default=None, help="各長さでの圧力の階段の往復回数（既定は plan）")
    ap.add_argument("--plan", default="tools/plans/pamtest_isometric.yaml")
    ap.add_argument("--note", default="")
    ap.add_argument("--mock", action="store_true", help="実機なしのドライラン")
    add_user_venue_args(ap)
    a = ap.parse_args()
    user, venue = require_user_venue(a.user, a.venue)
    cfg, calib, plan = load_config(), load_calib(), load_plan(a.plan)
    if a.mock:
        plan = {**plan, "hold_s": 0.4, "avg_s": 0.2, "ramp_s": 0.2, "zero_s": 0.5, "approach_settle_s": 0.3,
                "approach_rate_MPa_s": 0.3}
    cs = [float(c) for c in (a.contractions or plan["contractions_pct"])]
    if a.start_pct is not None:
        if a.start_pct not in cs:
            sys.exit(f"[isometric] --start_pct {a.start_pct} が長さの並び {cs} にありません")
        cs = cs[cs.index(a.start_pct):]

    stem = f"isometric_{a.pam}_{int(time.time())}"
    out_dir = run_dir(user, venue, "isometric")
    lim = cfg["limits"]
    print(f"[isometric] PAM {a.pam} / 長さ {cs} % / 保存先 {os.path.relpath(out_dir)}/{stem}.*")
    print(f"  自動停止: 内圧 > {lim['p_meas_max']} MPa, 力 > {lim['force_max_N']} N, "
          f"受信途絶 > {lim['rx_timeout_s']} s, 測定中の変位 > ±{lim['isometric_move_mm']} mm")

    rig = Rig(cfg, calib, mock=a.mock, isometric=True)
    rig.start()
    sess = Session(rig, plan, a)

    def save():
        meta = {"test": "isometric", "pam": a.pam, "contractions_pct": cs, "start_pct": a.start_pct,
                "ref_from": a.ref_from, "natural_ref_mm": rig.laser_ref_mm, "lengths": sess.lengths,
                "plan": plan, "plan_path": a.plan, "note": a.note, "mock": a.mock}
        return save_run(rig, out_dir, stem, meta, sess.steps)

    try:
        sess.run(cs, save)
        print("\n[isometric] 終了")
    except EStop as e:
        print(f"\n[isometric] 停止: {e.reason}")
    except KeyboardInterrupt:
        rig.trigger_estop("Ctrl+C / q")
    finally:
        rig.close()
        base = save()
        print(f"[Saved] {os.path.relpath(base)}.csv.gz / _steps.csv / .json（{len(sess.lengths)} 長さ、{len(sess.steps)} 段）")
        if sess.lengths:
            print("  長さ: " + ", ".join(f"{L['c_set']:g}→{L['c_actual']:.2f} %" for L in sess.lengths))


if __name__ == "__main__":
    main()
