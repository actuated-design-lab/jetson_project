"""
deploy_policy.py — 学習済みポリシーの実機デプロイ（全モデル共通）

旧 run_rl_deploy_midi.py の後継。違いは以下だけ:
  * モデル依存のパラメータ（lookahead / frame stack / LSTM有無）を
    models/manifest.yaml から取得し、ONNXの実体と突き合わせて検証する。
    → モデルを変えてもスクリプトを書き換えない。矛盾があれば起動時に落ちる。
  * frame stacking (モデルE) に対応。
  * --mock で実機なしのドライラン、--dump_obs でパリティ検証用のobs保存。
  * 実行条件（モデル/seed/trial番号/パケット統計）をサイドカーJSONに残す。

制御ループそのもの（非同期200Hz受信 / 50Hz絶対時刻同期 / 圧力変換）は
IROS時と同一。

使い方:
  python3 src/deploy_policy.py --model IROS/B --midi songs/test_single4_bpm60.mid
  python3 src/deploy_policy.py --model RAL/E  --midi songs/gmd_02_mid_bpm105.mid --trial 3
  python3 src/deploy_policy.py --model IROS/D --midi songs/test_double_bpm120.mid --mock
  python3 src/deploy_policy.py --list

出力先は data/<user>/<venue>/<group>_<日付>/。user / venue は --user / --venue か
環境変数 PORCARO_USER / PORCARO_VENUE で指定する（src/repo_paths.py）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from microlabbox import (MockLink, SensorReceiver, SENSOR_RATE_HZ,  # noqa: E402
                         open_serial, send_pressure)
from midi_rhythm_generator import MidiRhythmGenerator  # noqa: E402
from model_registry import (REPO_ROOT, PolicyRunner, list_models,  # noqa: E402
                            load_manifest, resolve)
from repo_paths import add_user_venue_args, require_user_venue, run_dir  # noqa: E402

QD_CLIP = 20.0   # 実機ノイズ対策の角速度クリップ [rad/s]（sim には無い。README参照）


def parse_args():
    p = argparse.ArgumentParser(description="Porcaro policy deployment (manifest driven)")
    p.add_argument("--model", type=str, help="モデルキー。例: IROS/B, RAL/E, B")
    p.add_argument("--midi", type=str, help="MIDIファイルのパス")
    p.add_argument("--bpm", type=float, default=None, help="BPMを上書き")
    p.add_argument("--trial", type=int, default=1, help="試行番号（ログに記録）")
    p.add_argument("--port", type=str, default=None, help="シリアルポート")
    p.add_argument("--baud", type=int, default=None, help="ボーレート")
    p.add_argument("--out", type=str, default=None,
                   help="出力ディレクトリ（省略時は data/<user>/<venue>/<group>_<日付>/）")
    p.add_argument("--verify", action="store_true", help="駆動しない確認モード")
    p.add_argument("--mock", action="store_true", help="実機なしのドライラン")
    p.add_argument("--no-input", action="store_true", help="ENTER待ちをしない")
    p.add_argument("--dump_obs", type=str, default=None,
                   help="obs/actionの系列を .npz に保存（パリティ検証用）")
    p.add_argument("--swap_encoders", action="store_true",
                   help="手首/ハンド関節エンコーダの配線が逆のとき、受信角度2chを入れ替える")
    p.add_argument("--usb_latency", type=int, choices=[1, 16], default=None,
                   help="USB変換の溜め込み時間[ms]。1 または 16。省略時は変更しない")
    p.add_argument("--action_mode", choices=["continuous", "binary"], default=None,
                   help="圧力指令の出し方を manifest から上書きする。binary = 電磁弁(0 / p_max)。"
                        "例: 連続値で学習したモデルを2値で動かす比較。binary の出力は既定で data/<user>/<venue>/binary_<日付>/ に分ける")
    p.add_argument("--list", action="store_true", help="manifest のモデル一覧を表示して終了")
    add_user_venue_args(p)
    return p.parse_args()


def read_latency_timer(port: str):
    """sysfs の latency_timer を読む。読めなければ None。"""
    name = os.path.basename(os.path.realpath(port))
    try:
        with open(f"/sys/bus/usb-serial/devices/{name}/latency_timer") as f:
            return int(f.read().strip())
    except Exception:  # noqa: BLE001
        return None


def git_rev() -> str:
    try:
        return subprocess.check_output(["git", "-C", REPO_ROOT, "rev-parse", "--short", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


class Deployer:
    def __init__(self, args):
        self.args = args
        manifest = load_manifest()
        defaults = manifest.get("defaults", {}) or {}

        self.spec = resolve(args.model, manifest)
        # --action_mode が指定されたら manifest の値を上書きする（記録用に出どころも残す）
        self.action_mode_source = "manifest"
        if args.action_mode and args.action_mode != self.spec.action_mode:
            self.spec.action_mode = args.action_mode
            self.action_mode_source = "cli_override"
        self.policy = PolicyRunner(self.spec, verbose=True)
        self.dt = self.spec.control_dt

        # --- リズム生成（先読み段数は manifest から。ハードコードしない） ---
        self.rhythm = MidiRhythmGenerator(
            midi_path=args.midi,
            device=torch.device("cpu"),
            dt=self.dt,
            target_force=self.spec.target_force,
            lookahead_steps=self.spec.lookahead_steps,
            override_bpm=args.bpm,
        )

        # --- 通信 ---
        if args.mock:
            print("[Link] MOCK モード: 実機には一切触りません")
            self.ser = MockLink(SENSOR_RATE_HZ)
        else:
            port = args.port or defaults.get("serial_port", "/dev/ttyUSB0")
            baud = args.baud or int(defaults.get("baud_rate", 230400))
            print(f"[Link] {port} @ {baud}")
            self.ser = open_serial(port, baud)
            if args.usb_latency is not None:
                self.ser.set_low_latency_mode(args.usb_latency == 1)
            print(f"[Link] latency_timer = {read_latency_timer(port)}")
        self.receiver = SensorReceiver(self.ser, swap_encoders=args.swap_encoders)
        self.receiver.start()

        self.prev_action = np.zeros(3, dtype=np.float32)
        self.prev_q = None
        self.cmd_logs: list[dict] = []
        self.obs_dump: list[np.ndarray] = []
        self.act_dump: list[np.ndarray] = []

    # ------------------------------------------------------------------ obs
    def build_base_frame(self, sensor: dict, t: float) -> tuple[np.ndarray, float]:
        """sim の _get_observations と同じ並びで base_obs_dim 次元を1フレーム作る。

        注意: 途中計算は float64 で行い、最後に float32 へ落とす。
        IROS時のコード (torch.cat(...).float()) が torch の型昇格で float64 計算
        → 最後に .float() だったため、その順序を保たないと 1e-6 オーダーでズレる。
        （tools/parity_test.py がビット一致を検査している）
        """
        q = np.array([np.radians(sensor["wrist_angle_deg"]),
                      np.radians(sensor["grip_angle_deg"])], dtype=np.float64)

        if self.prev_q is None:
            qd = np.zeros(2, dtype=np.float64)
        else:
            qd = np.clip((q - self.prev_q) / self.dt, -QD_CLIP, QD_CLIP)
        self.prev_q = q

        phase, traj = self.rhythm.get_state(t)
        traj = traj.detach().cpu().numpy().astype(np.float64)   # 既に0〜1に正規化済み

        frame = np.concatenate([
            q,                                                   # 2
            qd,                                                  # 2
            np.asarray(self.prev_action, np.float64),            # 3 (clip後)
            np.array([np.sin(phase), np.cos(phase)], np.float64),  # 2
            np.array([self.rhythm.bpm / 180.0], np.float64),     # 1
            traj,                                                # L
        ]).astype(np.float32)
        return frame, float(traj[0]) * self.spec.target_force

    # ------------------------------------------------------------------ run
    def run(self):
        a = self.args
        print("\n=== DEPLOY START ===")
        print(f"  model : {self.spec.key}  ({self.spec.label})")
        print(f"  song  : {a.midi}  (trial {a.trial})")
        mode = self.spec.action_mode
        if mode == "binary":
            mode += f" (0 / {self.spec.p_max} MPa, threshold {self.spec.binary_threshold} x p_max)"
        print(f"  action: {mode}  [{self.action_mode_source}]")
        print(f"  loop  : {1/self.dt:.0f}Hz control / {SENSOR_RATE_HZ:.0f}Hz recv")
        print(f"  encoder: {'SWAPPED (wrist<->grip)' if a.swap_encoders else 'as received'}")

        send_pressure(self.ser, 0, 0, 0)
        time.sleep(0.2 if a.mock else 2.0)
        if not (a.no_input or a.mock):
            input(">>> Press ENTER to Start... ")

        self.policy.reset()
        self.receiver.clear_for_sync()
        t_start = time.perf_counter()
        self.t_start = t_start
        step = 0

        try:
            while True:
                t = step * self.dt
                if t > self.rhythm.duration_sec:
                    print("\nSong finished.")
                    break

                sensor = self.receiver.get_latest()
                if sensor is None:
                    time.sleep(0.001)
                    continue

                frame, target_force = self.build_base_frame(sensor, t)
                obs = self.policy.build_obs(frame)

                if a.verify:
                    action = np.zeros(3, dtype=np.float32)
                    cmd = np.zeros(3, dtype=np.float32)
                    bar = "#" * int(max(0.0, target_force))
                    print(f"\r[Verify] t:{t:5.2f} F:{sensor['force_N']:6.1f} "
                          f"Tgt:{target_force:5.1f} {bar:10s}", end="")
                else:
                    action = self.policy.infer(obs)
                    action, cmd = self.policy.action_to_pressure(action)
                    self.prev_action = action
                    send_pressure(self.ser, *cmd)
                t_send = time.perf_counter()

                if a.dump_obs:
                    self.obs_dump.append(obs.reshape(-1).copy())
                    self.act_dump.append(np.asarray(action, np.float32).copy())

                self.cmd_logs.append({
                    "cmd_time": t, "target_force": target_force,
                    "action_DF": float(action[0]), "action_F": float(action[1]),
                    "action_G": float(action[2]),
                    "cmd_DF": float(cmd[0]), "cmd_F": float(cmd[1]), "cmd_G": float(cmd[2]),
                    "obs_t_recv": float(sensor["t_recv"]), "t_send": t_send,
                })

                step += 1
                t_next = t_start + step * self.dt
                while time.perf_counter() < t_next:
                    time.sleep(0.0005)

        except KeyboardInterrupt:
            print("\n[Stop] interrupted")
        finally:
            self.shutdown(time.perf_counter() - t_start)

    # ------------------------------------------------------------- shutdown
    def shutdown(self, elapsed: float):
        send_pressure(self.ser, 0, 0, 0)
        self.receiver.stop()
        time.sleep(0.05)
        try:
            self.ser.close()
        except Exception:  # noqa: BLE001
            pass
        self.save(elapsed)

    def save(self, elapsed: float):
        a = self.args
        sensor_logs = self.receiver.get_logs()
        if not sensor_logs:
            print("[Warn] センサデータが1件も取れていません。保存をスキップします。")
            return

        df = pd.DataFrame(sensor_logs)
        t_recv0 = df["t_recv"].iloc[0]
        df["t_recv_rel"] = df["t_recv"] - t_recv0          # 実測受信時刻（ジッタ確認用）
        df["time_recon"] = np.arange(len(df)) / SENSOR_RATE_HZ   # 旧定義（受信順×5ms）
        # 実際の発生時刻: 受信時刻の下側包絡に直線を当て、シリアル転送時間を引く
        k = np.arange(len(df))
        tr = df["t_recv"].to_numpy(float)
        coef = np.polyfit(k, tr, 1)
        env = np.percentile(tr - np.polyval(coef, k), 2)
        xfer = 58 * 10 / float(a.baud or 230400)
        df["time"] = np.polyval(coef, k) + env - xfer - getattr(self, "t_start", tr[0])

        if self.cmd_logs:
            dc = pd.DataFrame(self.cmd_logs).rename(columns={"cmd_time": "time"})
            df = pd.merge_asof(df, dc, on="time", direction="backward")

        # 既定の出力先: data/<user>/<venue>/<group>_<日付>/。2値で動かしたランは、連続値の実験フォルダ
        # （ral_* など）に混ざらないよう、どのモデルでも binary_<日付>/ に出す。
        # user / venue は main() で起動時に確定済み（実験の後で止まらないように）。
        out_group = "binary" if self.spec.action_mode == "binary" else self.spec.group.lower()
        out_dir = a.out or run_dir(a.user, a.venue, out_group)
        os.makedirs(out_dir, exist_ok=True)
        midi_name = os.path.splitext(os.path.basename(a.midi))[0]
        tag = "verify" if a.verify else f"{self.spec.group}-{self.spec.name}"
        if not a.verify and self.action_mode_source == "cli_override":
            tag += f"-{self.spec.action_mode}"
        stem = f"deploy_{midi_name}_{tag}_trial{a.trial:02d}_{int(time.time())}"

        csv_path = os.path.join(out_dir, stem + ".csv")
        df.to_csv(csv_path, index=False)

        # --- 2値で動かしたときは、実際に送った指令が 0 / p_max だけだったかを確認して残す ---
        cmd_binary_ok = None
        if self.spec.action_mode == "binary" and self.cmd_logs and not a.verify:
            sent = np.array([[c["cmd_DF"], c["cmd_F"], c["cmd_G"]] for c in self.cmd_logs])
            cmd_binary_ok = bool(np.all(np.isclose(sent, 0.0) | np.isclose(sent, self.spec.p_max)))

        # --- 実行条件のサイドカー（trial数・ckpt・パケット統計を必ず残す） ---
        expected = int(round(elapsed * SENSOR_RATE_HZ))
        meta = {
            "model_key": self.spec.key,
            "model_label": self.spec.label,
            "model_file": os.path.relpath(self.spec.path, REPO_ROOT),
            "arch": self.spec.arch,
            "lookahead_horizon": self.spec.lookahead_horizon,
            "lookahead_steps": self.spec.lookahead_steps,
            "frame_stack": self.spec.frame_stack,
            "obs_dim": self.spec.obs_dim,
            "manifest_extra": self.spec.extra,
            "midi": a.midi, "bpm": float(self.rhythm.bpm), "trial": a.trial,
            "control_dt": self.dt, "p_max": self.spec.p_max,
            "action_mode": self.spec.action_mode,
            "action_mode_source": self.action_mode_source,
            "binary_threshold": self.spec.binary_threshold if self.spec.action_mode == "binary" else None,
            "cmd_binary_ok": cmd_binary_ok,
            "time_basis": "gen_envelope", "t_start": getattr(self, "t_start", None),
            "usb_latency_arg": a.usb_latency,
            "latency_timer": read_latency_timer(a.port or "/dev/ttyUSB0"),
            "target_force": self.spec.target_force, "qd_clip": QD_CLIP,
            "mock": bool(a.mock), "verify": bool(a.verify),
            "swap_encoders": bool(a.swap_encoders),
            "elapsed_sec": round(elapsed, 3),
            "packets_received": self.receiver.n_packets,
            "packets_expected_at_200hz": expected,
            "packet_yield": round(self.receiver.n_packets / expected, 4) if expected else None,
            "bytes_discarded_on_resync": self.receiver.n_dropped,
            "control_steps": len(self.cmd_logs),
            "git_rev": git_rev(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        with open(os.path.join(out_dir, stem + ".json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        if a.dump_obs:
            os.makedirs(os.path.dirname(os.path.abspath(a.dump_obs)) or ".", exist_ok=True)
            np.savez(a.dump_obs, obs=np.array(self.obs_dump), action=np.array(self.act_dump))
            print(f"[Log] obs/action dump -> {a.dump_obs}")

        print(f"[Log] {csv_path}")
        print(f"[Log] {os.path.join(out_dir, stem + '.json')}")
        if cmd_binary_ok is not None:
            print(f"[Check] 送信した圧力指令が 0 / {self.spec.p_max} MPa だけか: {'OK' if cmd_binary_ok else 'NG'}")
        yield_pct = 100 * meta["packet_yield"] if meta["packet_yield"] else 0
        print(f"[Stat] 受信 {self.receiver.n_packets} / 期待 {expected} "
              f"({yield_pct:.1f}%), 再同期で捨てたバイト {self.receiver.n_dropped}")
        if meta["packet_yield"] and meta["packet_yield"] < 0.98:
            print("[Warn] パケット取りこぼしが多いです。ログの time 列（200Hz仮定の"
                  "再構成時刻）がずれている可能性があります。t_recv_rel と比較してください。")


def main():
    args = parse_args()
    if args.list:
        manifest = load_manifest()
        print("=== models/manifest.yaml ===")
        for key in list_models(manifest):
            spec = resolve(key, manifest)
            exists = "OK " if os.path.exists(spec.path) else "MISSING"
            print(f"[{exists}] {spec.describe()}\n")
        return
    if not args.model or not args.midi:
        print("--model と --midi は必須です（一覧は --list）", file=sys.stderr)
        sys.exit(2)
    if not args.out:
        args.user, args.venue = require_user_venue(args.user, args.venue)
    Deployer(args).run()


if __name__ == "__main__":
    main()

