"""
gen_pm_signals_rev.py — 圧力モデル再同定の2回目（10/6 の結果を受けて）

10/6 の結果：1 s 保持したあとの小ステップは大きさに関係なく通る。しきい値が出るのは
「小さい段で向きが反転し、しかも直前の変化から 0.2 s 以内」のとき（pm_rand）。
F のレギュレータは下げ（排気）が DF・G の約2倍遅い（pm_fg）。

  pm_rev    : DF。2段のステップ（1段目 ±d → 保持 h → 2段目）。2段目が
              反転（−d、元に戻る）か継続（さらに ±d）かを、d と h を変えて比べる
              d = 10/20/40/80 kPa, h = 40/80/120/200/300/500 ms, 中心 0.30
              → 「反転だけが通りにくい」か、「どのくらいの間隔なら通るか」を直接測る
  pm_ch_F   : F だけで pm_smallstep と同じ小ステップ＋正弦（2/3/4 Hz）＋反転（d 20/40, h 80/200/500 ms）
  pm_ch_G   : 同じものを G で
              → PAM ごとの違い（DR の範囲を決めるため）

DF 以外を振るファイルも、最初と最後に DF の同期ステップ（0.10→0.30→0.10）を入れる（エコーで時刻合わせ）。

使い方:
  python tools/gen_pm_signals_rev.py
"""
from __future__ import annotations

import numpy as np

from gen_pm_signals_1009 import REST, Seq, ch, const, sine   # 同じ書式・同じ休止値


def two_step(s, which, c, d, h, first, second, r, prefix=""):
    """c で保持 → c+first·d（h 秒）→ さらに second·d 動かす → 1 s 保持 → c に戻す"""
    kind = "rev" if first != second else "cont"
    p1 = c + first * d
    p2 = p1 + second * d
    tag = f"{prefix}{kind}_{'up' if first > 0 else 'dn'}_c{c}_d{d}_h{h}_r{r}"
    ch(s, which, const(c, 1.2), f"{prefix}settle_c{c}")
    ch(s, which, const(p1, h), f"{tag}_s1")
    ch(s, which, const(p2, 1.0), f"{tag}_s2")
    ch(s, which, const(c, 0.6), f"{prefix}back_c{c}")


def pm_rev(rng, c=0.30, sizes=(0.01, 0.02, 0.04, 0.08), holds=(0.04, 0.08, 0.12, 0.2, 0.3, 0.5)):
    s = Seq(); s.sync("sync0")
    conds = []
    for d in sizes:
        for h in holds:
            for first in (+1, -1):
                conds += [(d, h, first, -first, r) for r in range(2)]    # 反転 ×2
                conds += [(d, h, first, first, 0)]                       # 継続 ×1（対照）
    for i in rng.permutation(len(conds)):
        d, h, first, second, r = conds[i]
        cc = c if second == -first else c - first * d     # 継続は2段で 2d 動くので、中心をずらして範囲内に
        two_step(s, "DF", round(cc, 3), d, h, first, second, r)
    s.sync("sync1")
    return s


def pm_ch(rng, which):
    s = Seq(); s.sync("sync0")
    rest_v = REST[1] if which == "F" else REST[2]
    # 小ステップ（pm_smallstep と同じ形、80 kPa まで、各2回）
    conds = [(c, d) for c in (0.15, 0.30, 0.45) for d in (0.01, 0.02, 0.04, 0.08)]
    for r in range(2):
        for i in rng.permutation(len(conds)):
            c, d = conds[i]
            ch(s, which, const(c, 1.5), f"settle_c{c}")
            ch(s, which, const(c + d, 1.0), f"step_up_c{c}_d{d}_r{r}")
            ch(s, which, const(c, 1.0), f"step_back_c{c}_d{d}_r{r}")
            ch(s, which, const(c - d, 1.0), f"step_dn_c{c}_d{d}_r{r}")
            ch(s, which, const(c, 1.0), f"step_ret_c{c}_d{d}_r{r}")
    # 正弦（中心 0.30）
    conds = [(a, f) for f in (2.0, 3.0, 4.0) for a in (0.02, 0.04, 0.08, 0.12)]
    for i in rng.permutation(len(conds)):
        a, f = conds[i]
        ch(s, which, const(0.30, 1.0), "settle_c0.3")
        ch(s, which, sine(0.30, a, f), f"amp_c0.3_a{a}_f{f}")
    # 反転（中心 0.30）
    conds = [(d, h, first, r) for d in (0.02, 0.04) for h in (0.08, 0.2, 0.5) for first in (+1, -1) for r in range(2)]
    for i in rng.permutation(len(conds)):
        d, h, first, r = conds[i]
        two_step(s, which, 0.30, d, h, first, -first, r)
    ch(s, which, const(rest_v, 2.0), "rest")
    s.sync("sync1")
    return s


if __name__ == "__main__":
    rng = np.random.default_rng(1006)
    print("圧力モデル再同定 2回目の信号（優先度順）")
    total = 0.0
    for name, fn in [("pm_rev", pm_rev), ("pm_ch_F", lambda g: pm_ch(g, "F")), ("pm_ch_G", lambda g: pm_ch(g, "G"))]:
        total += fn(rng).save(name)
    print(f"  合計 {total / 60:.1f} 分（間の待ちを除く）")
