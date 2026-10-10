# figures_paper/ — JFPS 2026秋季 前刷の図

どれも `analysis/itv/` のスクリプトで作り直せる（実機データだけから作る。sim の出力は使わない）。

| ファイル | 作るスクリプト | 中身 |
|---|---|---|
| `fig_ampmap.{pdf,png}` | `analysis/itv/fig_ampmap.py` | 電空レギュレータの振幅比（出力 / 指令）。DF・F・G、指令振幅 20/40/80/120 kPa、破線は流量上限モデル |
| `fig_occupancy.{pdf,png}` | `analysis/itv/occupancy.py` | 打撃中の指令（RA-L 105本）の周波数 × 振幅の占有。赤線より左上がしきい値の領域（モデルAの表で 0.7 未満） |

再生（sim）と実機の比較図は sim 側（porcaro_2026 `analysis/eval/compare_replay.py`）で作る。
