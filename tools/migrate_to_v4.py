"""migrate_to_v4.py — v4再編（2026-10、data/<user>/<venue>/ への整理）の移行スクリプト。

何をするか
----------
v3 までの置き場所に残っているファイルを、新しい置き場所へ移す。

  IROS/                         → data/user0/iros2026/            （凍結アーカイブ。中身の構成はそのまま）
  data/ral_*/                   → data/user0/ral2026/ral_*/
  data/jfps_*/                  → data/user0/jfps2026/jfps_*/
  data/binary_*/                → data/user0/binary/binary_*/
  out/ral/                      → data/user0/ral2026/summary/
  test_signals/data_*.csv       → data/user0/jfps2026/playback_<日付>/   （信号再生の実測。日付はファイル名のunixtime）
  test_signals/（それ以外）     → signals/                         （入力信号）
  logs_verification/*.csv       → data/user0/jfps2026/verification_<日付>/
  pressure_swap_scan.csv        → data/user0/jfps2026/
  results/SI/, results/IROS/    → data/user1/si/<si|iros>_<日付>/  （6月の王さんのSI実験）
  tools/experiment_plan*.yaml   → tools/plans/

git が動かすのは追跡済みのファイルだけなので、Jetson 上で取ってまだ commit していないログは
git pull 後も旧フォルダに残る。それをこのスクリプトで同じ規則で移す。

使い方（リポジトリのルートで、git pull の後に）
------------------------------------------------
  python3 tools/migrate_to_v4.py          # 何が動くかを表示するだけ（既定）
  python3 tools/migrate_to_v4.py --apply  # 実際に移動する

挙動
----
- 移動先に同名のファイルが無ければ移動する。
- 同名で中身が同一なら旧側を消す（git が既に移した分）。
- 同名で中身が違えば何もせず「衝突」として報告する。
- 空になった旧フォルダは消す。何度実行しても安全。
"""
from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JST = timezone(timedelta(hours=9))

OLD_DIRS = ["IROS", "out", "test_signals", "logs_verification", "results"]


def day_from_unixtime(name: str) -> str:
    m = re.search(r"_(\d{10})(?=[_.])", name)
    if not m:
        return "undated"
    return datetime.fromtimestamp(int(m.group(1)), JST).strftime("%Y%m%d")


def day_from_stamp(name: str) -> str:
    m = re.search(r"_(\d{8})_\d{6}", name)
    return m.group(1) if m else day_from_unixtime(name)


def walk(rel_dir: str):
    base = os.path.join(ROOT, rel_dir)
    if not os.path.isdir(base):
        return
    for dp, _, fns in os.walk(base):
        for fn in fns:
            yield os.path.relpath(os.path.join(dp, fn), ROOT)


def plan() -> list[tuple[str, str]]:
    moves: list[tuple[str, str]] = []

    for f in walk("IROS"):
        moves.append((f, os.path.join("data/user0/iros2026", os.path.relpath(f, "IROS"))))

    data_dir = os.path.join(ROOT, "data")
    if os.path.isdir(data_dir):
        for d in sorted(os.listdir(data_dir)):
            venue = None
            if d.startswith("ral_"):
                venue = "ral2026"
            elif d.startswith("jfps_"):
                venue = "jfps2026"
            elif d.startswith("binary_"):
                venue = "binary"
            if venue and os.path.isdir(os.path.join(data_dir, d)):
                for f in walk(f"data/{d}"):
                    moves.append((f, os.path.join("data/user0", venue, os.path.relpath(f, "data"))))

    for f in walk("out/ral"):
        moves.append((f, os.path.join("data/user0/ral2026/summary", os.path.relpath(f, "out/ral"))))

    for f in walk("test_signals"):
        name = os.path.basename(f)
        if name.startswith("data_"):
            moves.append((f, f"data/user0/jfps2026/playback_{day_from_unixtime(name)}/{name}"))
        else:
            moves.append((f, os.path.join("signals", os.path.relpath(f, "test_signals"))))

    for f in walk("logs_verification"):
        name = os.path.basename(f)
        moves.append((f, f"data/user0/jfps2026/verification_{day_from_stamp(name)}/{name}"))

    if os.path.exists(os.path.join(ROOT, "pressure_swap_scan.csv")):
        moves.append(("pressure_swap_scan.csv", "data/user0/jfps2026/pressure_swap_scan.csv"))

    for grp, kind in (("SI", "si"), ("IROS", "iros")):
        for f in walk(f"results/{grp}"):
            name = os.path.basename(f)
            moves.append((f, f"data/user1/si/{kind}_{day_from_unixtime(name)}/{name}"))

    tools = os.path.join(ROOT, "tools")
    for name in sorted(os.listdir(tools)):
        if name.startswith("experiment_plan") and name.endswith(".yaml"):
            moves.append((f"tools/{name}", f"tools/plans/{name}"))

    return moves


def prune_empty(rel_dir: str) -> None:
    base = os.path.join(ROOT, rel_dir)
    if not os.path.isdir(base):
        return
    for dp, _, _ in sorted(os.walk(base), key=lambda x: -len(x[0])):
        try:
            os.rmdir(dp)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="実際に移動する（指定しなければ表示のみ）")
    args = ap.parse_args()

    print(f"=== v4 移行 [{'APPLY' if args.apply else 'DRY RUN（--apply で実行）'}] ===\n")
    moved, dedup, conflicts = 0, 0, []
    shown_dirs = set()
    for src, dst in plan():
        s, d = os.path.join(ROOT, src), os.path.join(ROOT, dst)
        if not os.path.exists(d):
            key = (os.path.dirname(src), os.path.dirname(dst))
            if key not in shown_dirs:
                print(f"  {key[0]}/  ->  {key[1]}/")
                shown_dirs.add(key)
            moved += 1
            if args.apply:
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.move(s, d)
        elif os.path.isfile(d) and filecmp.cmp(s, d, shallow=False):
            dedup += 1
            if args.apply:
                os.remove(s)
        else:
            conflicts.append((src, dst))

    if args.apply:
        for d in OLD_DIRS + ["data"]:
            prune_empty(d)
        os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)

    print(f"\n移動: {moved} ファイル / 同一のため旧側を削除: {dedup} ファイル")
    if conflicts:
        print(f"\n[衝突] 移動先に中身の違う同名ファイルがあるため残したもの: {len(conflicts)} 件")
        for s, d in conflicts[:30]:
            print(f"  {s}  vs  {d}")
    left = [d for d in OLD_DIRS if os.path.exists(os.path.join(ROOT, d))]
    if args.apply and left:
        print("\n[残り] まだ存在する旧フォルダ（中身を確認して手で整理すること）: " + ", ".join(left))
    return 1 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
