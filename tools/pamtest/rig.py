"""rig.py — PAM 単体試験機（等張性・等尺性）の共通部品

配線（2026/10 の試験機。通信はデプロイと同じ 230400 bps・受信 7 要素のまま）
  送信 Jetson -> MLB : [cmd_DF, cmd_F, cmd_G]  … 使うのは DF の ITV だけ。F・G は常に 0 を送る
  受信 MLB -> Jetson : [P_DF, (F の圧力 → レーザ変位計), P_G, wrist, grip, flag, force_N]
      内圧    : 試験する PAM の内圧 [MPa]。どの圧力センサの入力かは rig_config.yaml の channels.pressure
                （meas_pres_DF / meas_pres_G）。2026/10/20 から DF のセンサが読めないので G のセンサを使用
      laser   : レーザ変位計。F の圧力センサの代わりに同じ入力へ挿したので、値は Simulink の
                圧力の換算を通った「生の値」。mm への換算は laser_calib.json（check.py の cal で作る）
      force_N : ロードセル（これまでと同じ位置）。等尺性だけで使う。等張性は記録だけ（力の上限でも止めない）
      flag    : MicroLabBox 側だけで使う。記録はするが Jetson は見ない
      P_G / wrist / grip : 使わない（記録だけ）

安全
  * 指令は rig_config.yaml の p_cmd_max でクリップ
  * 監視（50 Hz）：内圧の上限・力の上限・受信の途絶・（等尺性では）変位の動き → 越えたら即 0 MPa にして止める
  * 非常停止キー：自動で動いている間はスペース / e / q / Esc のどれかで即 0 MPa（Ctrl+C も同じ）
  * どの終わり方でも 0 MPa を数回送ってから閉じる
"""
from __future__ import annotations

import atexit
import json
import os
import select
import struct
import subprocess
import sys
import threading
import time

import numpy as np
import pandas as pd
import yaml

PAMTEST_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(PAMTEST_DIR))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
from microlabbox import (HEADER, RECV_FMT, MockLink,  # noqa: E402
                         SensorReceiver, open_serial, send_pressure)

CONFIG_PATH = os.path.join(PAMTEST_DIR, "rig_config.yaml")
CALIB_PATH = os.path.join(PAMTEST_DIR, "laser_calib.json")
CONTROL_DT = 0.02          # 指令の送信は 50 Hz（デプロイ・信号再生と同じ）
SENSOR_DT = 0.005          # MicroLabBox の受信は 200 Hz


class EStop(Exception):
    """非常停止（キー・監視・Ctrl+C）。reason に理由。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# =============================================================================
# 設定
# =============================================================================
def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_calib(path: str = CALIB_PATH) -> dict:
    """レーザの換算 mm = gain * raw + offset と、収縮の向き sign。無ければ未校正（gain=1, offset=0）。"""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
        c.setdefault("sign", 1.0)
        c["calibrated"] = True
        return c
    return {"gain": 1.0, "offset": 0.0, "sign": 1.0, "calibrated": False}


def save_calib(c: dict, path: str = CALIB_PATH) -> None:
    c = {k: v for k, v in c.items() if k != "calibrated"}
    c["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)


def git_rev() -> str:
    try:
        return subprocess.check_output(["git", "-C", REPO_ROOT, "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def load_plan(path: str) -> dict:
    if not os.path.exists(path):
        alt = os.path.join(REPO_ROOT, "tools", "plans", os.path.basename(path))
        path = alt if os.path.exists(alt) else path
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def staircase(levels: list[float], updown: bool = True) -> list[tuple[float, str]]:
    """[0, .1, .2] → [(0,'up'), (.1,'up'), (.2,'up'), (.1,'down'), (0,'down')]"""
    seq = [(float(p), "up") for p in levels]
    if updown:
        seq += [(float(p), "down") for p in reversed(levels[:-1])]
    return seq


# =============================================================================
# 非常停止キー
# =============================================================================
class KeyWatcher(threading.Thread):
    """armed の間だけ stdin を 1 文字ずつ見て、停止キーで on_stop を呼ぶ。
    input() で人に入力してもらう間は disarm() しておく（端末を通常モードに戻す）。"""

    STOP_KEYS = {" ", "e", "E", "q", "Q", "\x1b"}

    def __init__(self, on_stop):
        super().__init__(daemon=True)
        self.on_stop = on_stop
        self.tty = sys.stdin.isatty()
        self._armed = threading.Event()
        self._old = None
        if self.tty:
            import termios
            self._old = termios.tcgetattr(sys.stdin.fileno())
            atexit.register(self._restore)

    def arm(self):
        if self.tty and not self._armed.is_set():
            import tty
            tty.setcbreak(sys.stdin.fileno())
        self._armed.set()

    def disarm(self):
        self._armed.clear()
        time.sleep(0.06)            # 読み取り中の select を抜けさせる
        self._restore()

    def _restore(self):
        if self.tty and self._old is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old)

    def run(self):
        if not self.tty:
            return                  # パイプ実行（テスト）ではキー停止なし。Ctrl+C は効く
        while True:
            if not self._armed.wait(0.1):
                continue
            r, _, _ = select.select([sys.stdin], [], [], 0.05)
            if r and self._armed.is_set():
                ch = sys.stdin.read(1)
                if ch in self.STOP_KEYS:
                    self.on_stop(f"key '{ch.strip() or 'space'}'")


# =============================================================================
# ドライラン用の疑似試験機
# =============================================================================
class MockRig(MockLink):
    """MockLink を試験機用に置き換えたもの。物理は大雑把（UI と記録の流れを確かめる用）。
    load_N（等張性のおもり）か fixed_h（等尺性の収縮率）を外から入れる。"""

    def __init__(self, calib: dict, L0_mm: float):
        super().__init__()
        self.calib, self.L0 = calib, L0_mm
        self.p = 0.0
        self.load_N = 0.0
        self.fixed_h = None          # 等尺性のとき収縮率（0〜1）。None なら治具がゆるんで自由に動く
        self.h = 0.0                 # いまの収縮率（治具を固定するときに使う）
        self.rng = np.random.default_rng(0)

    def _generate(self):
        now = time.perf_counter() - self.t0
        should = int(now / self.dt)
        while self.n_emitted < should:
            self.p += (self._last_cmd[0] - self.p) * self.dt / 0.08
            p = max(self.p, 0.0)
            hmax = 0.25 * min(p / 0.6, 1.0)                  # 自由収縮
            k = 20.0 + 1500.0 * p                            # 力 = k (hmax - h) [N]
            if self.fixed_h is None:
                h = hmax - self.load_N / k
                force = self.load_N
            else:
                h = self.fixed_h
                force = max(k * (hmax - h), 0.0)
            self.h = h
            mm = 50.0 + self.calib.get("sign", 1.0) * h * self.L0
            raw = (mm - self.calib["offset"]) / self.calib["gain"]
            self._pending += HEADER + struct.pack(
                RECV_FMT, p + self.rng.normal(0, 0.001), raw + self.rng.normal(0, 0.002), p,
                0.0, 0.0, 1.0, force + self.rng.normal(0, 0.2))
            self.n_emitted += 1


# =============================================================================
# 試験機
# =============================================================================
class Rig:
    """送信スレッド（50 Hz、指令と安全監視）＋受信スレッド（200 Hz、src/microlabbox.py）。

    使い方:
        rig = Rig(cfg, calib, mock=False, isometric=False)
        rig.start()
        rig.ramp_to(0.3, 1.0); rig.hold(5.0, tag={...})
        rig.close()
    """

    def __init__(self, cfg: dict, calib: dict, mock: bool = False, isometric: bool = False,
                 use_force: bool = True):
        self.cfg, self.calib = cfg, calib
        self.use_force = use_force          # False（等張性）なら力の上限で止めない（ロードセルは記録だけ）
        self.lim = cfg["limits"]
        self.isometric = isometric
        self.p_key = cfg.get("channels", {}).get("pressure", "meas_pres_DF")   # 内圧を読む受信の列
        self.mock = mock
        if mock:
            self.ser = MockRig(calib, cfg["pam"]["L0_mm"])
        else:
            self.ser = open_serial(cfg["serial"]["port"], cfg["serial"]["baud"])
        self.rx = SensorReceiver(self.ser)
        self._lock = threading.Lock()
        self._target = 0.0
        self._tag: dict = {}
        self.cmd_logs: list[dict] = []
        self.events: list[dict] = []
        self.estop_reason: str | None = None
        self._estop_evt = threading.Event()
        self._running = False
        self.laser_ref_mm: float | None = None      # 長さの基準（等張性：吊り具だけ、等尺性：自然長）。表示の Δ
        self.move_ref_mm: float | None = None       # 等尺性で治具を固定した位置。ここから動いたら止める（None で監視しない）
        self.keys = KeyWatcher(self.trigger_estop)
        self._last_n = 0
        self._last_rx_t = None
        atexit.register(self._zero_and_close)

    # ---- 起動・終了 -------------------------------------------------------
    def start(self, wait_s: float = 1.0):
        self.rx.start()
        self._running = True
        self._tx = threading.Thread(target=self._tx_loop, daemon=True)
        self._tx.start()
        self.keys.start()
        t_end = time.perf_counter() + 3.0
        while self.rx.get_latest() is None:
            if time.perf_counter() > t_end:
                self.close()
                sys.exit("[rig] MicroLabBox からの受信がありません（ケーブル・Simulink・ボーレートを確認）")
            time.sleep(0.05)
        self.rx.clear_for_sync()
        time.sleep(wait_s)
        self.event("start")

    def close(self):
        self._zero_and_close()

    def _zero_and_close(self):
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self._running = False
        try:
            self.cmd_logs.append({"t_pc": time.perf_counter(), "cmd_DF": 0.0, "phase": "closed"})
            for _ in range(5):
                send_pressure(self.ser, 0.0, 0.0, 0.0)
                time.sleep(CONTROL_DT)
        except Exception:  # noqa: BLE001
            pass
        self.keys.disarm() if self.keys.is_alive() else None
        self.rx.stop()
        time.sleep(0.05)
        try:
            self.ser.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- 非常停止 ---------------------------------------------------------
    def trigger_estop(self, reason: str):
        if self._estop_evt.is_set():
            return
        self.estop_reason = reason
        self._estop_evt.set()
        with self._lock:
            self._target = 0.0
        try:
            send_pressure(self.ser, 0.0, 0.0, 0.0)
        except Exception:  # noqa: BLE001
            pass
        self.cmd_logs.append({"t_pc": time.perf_counter(), "cmd_DF": 0.0, "phase": "ESTOP"})
        self.event("ESTOP", reason=reason)
        print(f"\n\n!!! 非常停止: {reason} → 0 MPa にしました !!!\n", flush=True)

    def check_estop(self):
        if self._estop_evt.is_set():
            raise EStop(self.estop_reason or "estop")

    @property
    def stopped(self) -> bool:
        return self._estop_evt.is_set()

    # ---- 送信と監視（50 Hz） ----------------------------------------------
    def _tx_loop(self):
        t0 = time.perf_counter()
        i = 0
        while self._running:
            with self._lock:
                p = 0.0 if self._estop_evt.is_set() else min(max(self._target, 0.0), self.lim["p_cmd_max"])
                tag = dict(self._tag)
            try:
                send_pressure(self.ser, p, 0.0, 0.0)
            except Exception as e:  # noqa: BLE001
                self.trigger_estop(f"送信エラー {e}")
            now = time.perf_counter()
            self.cmd_logs.append({"t_pc": now, "cmd_DF": p, **tag})
            self._monitor(now)
            i += 1
            nxt = t0 + i * CONTROL_DT
            while time.perf_counter() < nxt:
                time.sleep(0.0005)

    def _monitor(self, now: float):
        if self._estop_evt.is_set():
            return
        s = self.rx.get_latest()
        n = self.rx.n_packets
        if n != self._last_n:
            self._last_n, self._last_rx_t = n, now
        elif self._last_rx_t is not None and now - self._last_rx_t > self.lim["rx_timeout_s"]:
            self.trigger_estop(f"受信が {self.lim['rx_timeout_s']} s 途絶")
            return
        if s is None:
            return
        if s[self.p_key] > self.lim["p_meas_max"]:
            self.trigger_estop(f"内圧 {s[self.p_key]:.3f} MPa > {self.lim['p_meas_max']}")
        elif self.use_force and abs(s["force_N"]) > self.lim["force_max_N"]:
            self.trigger_estop(f"力 {s['force_N']:.1f} N > {self.lim['force_max_N']} N")
        elif self.isometric and self.move_ref_mm is not None:
            d = self.laser_mm(s["meas_pres_F"]) - self.move_ref_mm
            if abs(d) > self.lim["isometric_move_mm"]:
                self.trigger_estop(f"等尺性なのに {d:+.2f} mm 動いた（治具のずれ・破損？）")

    # ---- 指令 ---------------------------------------------------------------
    def set_tag(self, **tag):
        with self._lock:
            self._tag = tag

    def set_pressure(self, p: float):
        with self._lock:
            self._target = float(p)

    @property
    def target(self) -> float:
        with self._lock:
            return self._target

    def ramp_to(self, p: float, ramp_s: float):
        """今の指令から p まで ramp_s 秒で直線に変える（0 なら即）。"""
        p0 = self.target
        n = max(int(round(ramp_s / CONTROL_DT)), 1)
        for k in range(1, n + 1):
            self.check_estop()
            self.set_pressure(p0 + (p - p0) * k / n)
            time.sleep(CONTROL_DT)
        self.check_estop()

    def wait(self, sec: float, status: bool = True):
        t_end = time.perf_counter() + sec
        t_print = 0.0
        while time.perf_counter() < t_end:
            self.check_estop()
            if status and time.perf_counter() - t_print > 0.25:
                self.print_status(f"残り {t_end - time.perf_counter():4.1f} s")
                t_print = time.perf_counter()
            time.sleep(0.02)
        self.check_estop()

    # ---- 計測値 -------------------------------------------------------------
    def laser_mm(self, raw: float) -> float:
        return self.calib["gain"] * raw + self.calib["offset"]

    def window(self, sec: float) -> pd.DataFrame:
        """直近 sec 秒の受信サンプル。"""
        logs = self.rx.get_logs()
        n = max(int(sec / SENSOR_DT), 1)
        df = pd.DataFrame(logs[-n:])
        df["laser_mm"] = self.laser_mm(df["meas_pres_F"])
        return df

    def mean(self, sec: float) -> dict:
        df = self.window(sec)
        return {"P_meas": df[self.p_key].mean(), "laser_raw": df.meas_pres_F.mean(),
                "laser_mm": df.laser_mm.mean(), "laser_mm_std": df.laser_mm.std(),
                "force_N": df.force_N.mean(), "force_N_std": df.force_N.std(), "n": len(df)}

    def print_status(self, extra: str = ""):
        s = self.rx.get_latest()
        if s is None:
            return
        mm = self.laser_mm(s["meas_pres_F"])
        rel = "" if self.laser_ref_mm is None else f" Δ{(mm - self.laser_ref_mm) * self.calib['sign']:+6.2f}mm"
        sys.stdout.write(f"\r  指令 {self.target:5.3f} | 内圧 {s[self.p_key]:6.3f} MPa | "
                         f"変位 {mm:8.3f} mm{rel} | 力 {s['force_N']:7.2f} N | {extra}   ")
        sys.stdout.flush()

    def event(self, name: str, **kw):
        self.events.append({"t_pc": time.perf_counter(), "event": name, **kw})

    # ---- 保存 ---------------------------------------------------------------
    def to_frame(self) -> pd.DataFrame:
        """受信 200 Hz に、その時点の指令とタグ（段・おもり・向き）を前詰めで付けた表。"""
        sens = pd.DataFrame(self.rx.get_logs())
        if sens.empty:
            return sens
        sens = sens.rename(columns={"meas_pres_F": "laser_raw", "t_recv": "t_pc"})
        sens["laser_mm"] = self.laser_mm(sens["laser_raw"])
        sens["P_meas"] = sens[self.p_key]                 # 試験する PAM の内圧（channels.pressure の列）
        sens["time"] = np.arange(len(sens)) * SENSOR_DT
        sens["t_recv_rel"] = sens["t_pc"] - sens["t_pc"].iloc[0]
        cmd = pd.DataFrame(list(self.cmd_logs)).sort_values("t_pc")
        out = pd.merge_asof(sens.sort_values("t_pc"), cmd, on="t_pc", direction="backward")
        front = ["time", "t_recv_rel", "cmd_DF", "P_meas", "meas_pres_DF", "meas_pres_G", "laser_raw", "laser_mm",
                 "force_N", "flag"]
        rest = [c for c in out.columns if c not in front and c != "t_pc"]
        return out[front + rest]


def save_run(rig: Rig, out_dir: str, stem: str, meta: dict, steps: list[dict]) -> str:
    """<stem>.csv.gz（200 Hz の全記録、gzip・有効数字 7 桁）、<stem>_steps.csv（段ごとの平均）、<stem>.json（条件）。
    途中保存にも使う。全記録はそのままの CSV だと 30 分で 100 MB を超え、GitHub に push できないので圧縮する
    （pandas.read_csv でそのまま読める）。"""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, stem)
    df = rig.to_frame()
    if not df.empty:
        df.to_csv(base + ".csv.gz", index=False, float_format="%.7g", compression="gzip")
    if steps:
        pd.DataFrame(steps).to_csv(base + "_steps.csv", index=False)
    n = rig.rx.n_packets
    cmd_logs = list(rig.cmd_logs)
    dur = len(cmd_logs) * CONTROL_DT
    meta = {**meta,
            "git_rev": git_rev(),
            "config": rig.cfg,
            "laser_calib": rig.calib,
            "laser_ref_mm": rig.laser_ref_mm,
            "pressure_channel": rig.p_key,
            "estop_reason": rig.estop_reason,
            "n_packets": n,
            "packet_yield": round(n / max(dur / SENSOR_DT, 1), 4),
            "n_dropped_bytes": rig.rx.n_dropped,
            "events": [{**e, "t_rel": e["t_pc"] - (cmd_logs[0]["t_pc"] if cmd_logs else 0)}
                       for e in rig.events],
            "saved": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=float)
    return base
