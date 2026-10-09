# ITV（電空レギュレータ）のしきい値を入れた圧力モデル — 解析一式（2026/10/8）

実機データ（このリポジトリの `data/*/*/playback_*/` の実測と `signals/` の入力信号）を読むだけで、sim の出力は使わない。別の場所のデータを読むときだけ `JETSON_PROJECT` を指定。
numba・scipy・pandas・matplotlib が必要（torch は不要）。

```
python analysis/itv/fig_models.py      # O / A / B / B＋漏れの指標の表と models_compare.png
python analysis/itv/check_fg.py        # DF の同定値を F・G チャネルに当てたときの確認
python analysis/itv/model_BL.py 0.7    # B＋漏れの同定し直し（数分）
```

| ファイル | 中身 |
|---|---|
| `pmeval.py` | 評価の共通部分。DF チャネル、エコーの指令を入力。同定用（ステップ・正弦・小ステップ・2段ステップ）と検証用（ランダム段・ランプ・階段・tm_C/D/E）に分けて、NRMSE・振幅比マップ・t50・反転を出す |
| `fit_common.py` | 同定の残差（時間波形＋振幅比マップ） |
| `base_eval.py` | 流量上限モデル O（ベースライン） |
| `model_A.py` | モデルA（現象論）：振幅比マップから表 G(f, A) を作り、推定器の設定をグリッド探索 → `fit_A.json` |
| `model_B.py` | モデルB（パイロット段＋主弁の重なり）：時間波形＋マップで同時同定 → `fit_B2_w0.7.json`（引数はマップの重み、0.7 で使用） |
| `model_BL.py` | モデルB＋漏れ（重なりの中でも主弁が x に比例して少し漏れる。`kl` を追加）：B の同定値から始めて同時同定 → `fit_BL_w0.7.json` |
| `check_fg.py` | DF で同定したモデルを F・G チャネル（`pm_ch_F` / `pm_ch_G`）に当てたときの NRMSE・t50・振幅比 |
| `fig_models.py` | O / A / B / B＋漏れ の比較図と指標の表 |
| `rate_check.py` | 制御周期を下げたときの圧力の変化（10/8） |
| `struct_vs_data.py` | 構造×同定データの比較（10/7）→ `struct_vs_data_fits.json`（O のパラメータ） |

10/8 昼：B＋漏れを追加。model_B.py の保存先の書き間違い（`fos.path`）を修正。

sim 側は porcaro_2026 の `scripts/itv_models.py`（`replay_open_loop.py --pmodel shaped / pilot / pilot_leak`）。パラメータを変えたら、そちらの `DEFAULTS` も合わせて直す。

## 既知の限界（10/8）
- 3チャネルとも DF の同定値。F は排気が遅く、2 Hz・小振幅では F・G とも実機はほぼ素通り（A は 0.7、B＋漏れは 0.5 まで削る）
- B＋漏れは 1 s 保持後の孤立した小ステップを遅くしすぎる（DF 下げ 10〜20 kPa の t50：実機 60〜65 ms、B＋漏れ 160 ms）
