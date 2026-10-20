"""compress_logs.py — 古い形式（無圧縮の .csv）で保存した試験機の全記録を .csv.gz に変換する

2026/10/20 の朝までの isotonic.py / isometric.py / check.py は 200 Hz の全記録を無圧縮の CSV で保存していて、
30 分の試験で 100 MB を超え GitHub に push できなかった。今の版と同じ形式（gzip、有効数字 7 桁）に変換する。
_steps.csv（段ごとの平均、小さい）はそのまま。変換して読み戻せることを確かめてから元の .csv を消す。

  python3 tools/pamtest/compress_logs.py --dir data/user0/pamtest            # 何が変わるかの表示だけ
  python3 tools/pamtest/compress_logs.py --dir data/user0/pamtest --apply    # 実行
"""
from __future__ import annotations

import argparse
import glob
import os

import numpy as np
import pandas as pd


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    files = [f for f in sorted(glob.glob(os.path.join(a.dir, "**", "*.csv"), recursive=True))
             if not f.endswith("_steps.csv")]
    if not files:
        print("変換するファイルはありません")
        return
    for f in files:
        mb = os.path.getsize(f) / 1e6
        if not a.apply:
            print(f"  {os.path.relpath(f)}  {mb:7.1f} MB → .csv.gz")
            continue
        df = pd.read_csv(f)
        out = f + ".gz"
        df.to_csv(out, index=False, float_format="%.7g", compression="gzip")
        back = pd.read_csv(out)
        num = df.select_dtypes("number").columns
        ok = (back.shape == df.shape and
              np.allclose(back[num].to_numpy(float), df[num].to_numpy(float), rtol=1e-6, atol=1e-9, equal_nan=True))
        if not ok:
            os.remove(out)
            print(f"  ✗ {os.path.relpath(f)}：読み戻した値が合わないので元のまま残しました")
            continue
        os.remove(f)
        print(f"  ✓ {os.path.relpath(f)}  {mb:7.1f} MB → {os.path.getsize(out) / 1e6:5.1f} MB")
    if not a.apply:
        print("\n--apply を付けると変換します（元の .csv は消えます）")


if __name__ == "__main__":
    main()
