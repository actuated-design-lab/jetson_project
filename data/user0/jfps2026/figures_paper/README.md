# figures_paper/ — JFPS 2026秋季 前刷の図

どれも `analysis/itv/` のスクリプトで作り直せる（実機データだけから作る。sim の出力は使わない）。

| ファイル | 作るスクリプト | 中身 |
|---|---|---|
| `fig_ampmap.{pdf,png}` | `analysis/itv/fig_ampmap.py` | 電空レギュレータの振幅比（出力 / 指令）。DF・F・G、指令振幅 20/40/80/120 kPa、破線は流量上限モデル |
| `fig_occupancy.{pdf,png}` | `analysis/itv/occupancy.py` | 打撃中の指令（RA-L 105本）の周波数 × 振幅の占有。赤線より左上がしきい値の領域（モデルAの表で 0.7 未満） |
| `fig_threshold.{pdf,png}` | `analysis/itv/fig_threshold.py` | しきい値の出る条件（DF）：(a) 1 s 保持後の小ステップの 50 % 到達時間、(b) 40 kPa の持続した正弦の振幅比 |
| `fig_waveform.{pdf,png}` | `analysis/itv/fig_waveform.py` | 従来（テーブル）と流量上限モデルの圧力波形：(a) 2月のチャープ、(b) 打撃中の DF |
| `fig_rate.{pdf,png}` | `analysis/itv/rate_check.py` | 指令周期を粗くしたときの 0.3 MPa 通過時刻のずれ（前刷では紙面の都合で本文のみ） |

再生（sim）と実機の比較図は sim 側（porcaro_2026 `analysis/eval/compare_replay.py`）で作る。
