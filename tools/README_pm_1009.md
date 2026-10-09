# 10/9 測定計画：圧力モデルの再同定（しきい値の構造を見分ける）

JFPS 2026秋。「速くて小さい指令が通らない」しきい値（9/30 振幅×周波数マップ）をモデルに入れるためのデータを取る。
**見るのは圧力だけ**（関節角・打面は関係なし）。

## 何を確かめたいか

今の候補は「パイロット段が誤差を積分し、主弁は不感帯を越えたときだけ開く」構造
（ITV の内部：給排気の電磁弁 → パイロット室 → ダイアフラム → 主弁）。
9/30 のマップ（正弦）だけだと、この構造と他の構造（単純な不感帯、振幅に依らないフィルタ）を見分けにくいので、
ステップとランプで直接確かめる。

| ファイル | 中身 | 候補の構造が正しければ | 違う場合 |
|---|---|---|---|
| `pm_smallstep` | 小ステップ 5/10/20/40/80 kPa、上げ・下げ、中心 0.15/0.30/0.45、各3回 | 小さいステップほど動き出しが遅い（t50 が伸びる）。最後は届く | t50 一定で届かない → 単純な不感帯。t50 一定で届く → しきい値は正弦のときだけ |
| `pm_ramp` | ランプ 0.05〜2 MPa/s、中心 0.15/0.30/0.45（±0.08 MPa）、各2回 | ゆっくりなランプで遅れが 1/傾き に比例して伸びる | 遅れ一定 → むだ時間だけ |
| `pm_sine2` | 正弦 1.5/2.5/4/5 Hz × 振幅 10〜120 kPa（中心 0.30）、4 Hz は中心 0.15/0.45 も | マップの穴埋め（9/30 は 1/2/3/6 Hz）。位相も同定に使う | ― |
| `pm_rand` | 保持 40〜400 ms のランダムな段の列（90 s） | **同定に使わない**時間領域の検証用 | ― |
| `pm_stairs` | 10 kPa 刻みの階段 0.10→0.50→0.10、5 kPa 刻み 0.25→0.35→0.25 | 小さい変化の積み重ねでの追従 | ― |
| `pm_fg` | F と G で小ステップ（10/20/40 kPa）と 3 Hz 正弦 | DF と同じ振る舞いなら、sim の3チャネルに同じモデルを使ってよい | 違えばチャネルごとに同定 |

共通：DF 以外は F=0.10, G=0.30 固定（`pm_fg` を除く）。各ファイルの最初と最後に DF の同期ステップ（0.10→0.30→0.10）があり、エコーで時刻を合わせる。
信号は `tools/gen_pm_signals_1009.py` で生成（seed 固定なので、生成し直しても同じもの）。

## 優先度と時間

| 順 | ファイル | 長さ |
|---|---|---|
| 1 | pm_smallstep | 4.3 分 |
| 2 | pm_ramp | 3.5 分 |
| 3 | pm_sine2 | 2.9 分 |
| 4 | pm_rand | 1.6 分 |
| 5 | pm_stairs | 2.7 分 |
| 6 | pm_fg | 1.7 分 |
| | 合計 | 約17分（＋各回の確認） |

時間が足りなければ 1〜4 だけでよい。

## 事前の確認

- 元圧が来ている
- ControlDesk でエコー版のアプリケーションを読み込み済み（flag が指令のエコーになる）
- `latency_timer` = 1
- 打面は外す（なくてもよいが、10/3 と同じ条件にそろえる）

## 手順（Jetson、コンテナ内）

```
cd /data/jetson_project
git pull
export PORCARO_USER=user0 PORCARO_VENUE=jfps2026   # 実測の保存先 data/user0/jfps2026/playback_<日付>/
cat /sys/bus/usb-serial/devices/ttyUSB0/latency_timer      # 1
python tools/run_signal_playback.py pm_smallstep
python tools/check_pm_data.py pm_smallstep                 # ★ が出たら止めて確認
```

残りも同じく `run_signal_playback.py <名前>` → `check_pm_data.py <名前>` を順に。

まとめて流す場合：
```
for s in pm_smallstep pm_ramp pm_sine2 pm_rand pm_stairs pm_fg; do
  python tools/run_signal_playback.py $s && python tools/check_pm_data.py $s; sleep 5
done
```

## `check_pm_data.py` の見方

- 長さ・受信の空き・エコー・元圧の4行がすべて OK なら、そのデータは使える
- `pm_smallstep`：ステップの大きさごとの t50（50% に届くまでの時間）と到達率。5〜10 kPa の t50 が 40〜80 kPa より明らかに長ければ、候補の構造と合う
- `pm_ramp`：傾きごとの遅れ。ゆっくりなほど遅れが伸びるかを見る
- `pm_sine2`：中心 0.30 の振幅比の表。9/30 と同じ傾向（4〜5 Hz の小振幅がほぼ 0）なら OK

## 終わったら

ホストでいつもどおり chown してから
```
git add data/user0/jfps2026/playback_*/
git commit -m "JFPS 10/9: pressure model re-identification data"
git push
```

## メモ

- 実機ログは圧力の列が入れ替わっている：物理的な DF = `meas_pres_F` 列、物理的な F = `meas_pres_DF` 列（9/29 判明）。`check_pm_data.py` は戻してから使っている
- 解析（同時同定）は持ち帰ってから。目的関数は振幅比・位相・ステップ波形・ランプをまとめて入れ、`pm_rand` とテスト動作 C/E で検証する

---

# 2回目（10/6 の結果を受けて）：反転としきい値、PAM ごとの違い

## 10/6 の結果（1回目）
- 1 s 保持したあとの小ステップ（5〜80 kPa）は、大きさに関係なく同じ速さで通り、最後まで届く → 「積分＋不感帯」の仮説は外れ
- しきい値が出るのは「小さい段（≤30 kPa）で向きが反転し、直前の変化から 0.2 s 以内」のとき（pm_rand：実機 0.16 / O モデル 0.45, 43件）
- 上げは下げより 60〜100 ms 遅い。F は下げ（排気）が DF・G の約2倍遅い（t50 160 ms vs 75 ms）

## 信号（`tools/gen_pm_signals_rev.py`）
| 順 | ファイル | 中身 | 長さ |
|---|---|---|---|
| 1 | `pm_rev` | DF の2段ステップ：±d → 保持 h → 反転（元に戻る）または継続（さらに同じ向き）。d = 10/20/40/80 kPa, h = 40〜500 ms | 7.3 分 |
| 2 | `pm_ch_F` | F だけで 小ステップ（10〜80 kPa）＋正弦（2/3/4 Hz）＋反転 | 4.4 分 |
| 3 | `pm_ch_G` | 同じものを G で | 4.4 分 |

## 手順（Jetson、コンテナ内）
```
cd /data/jetson_project
git pull
export PORCARO_USER=user0 PORCARO_VENUE=jfps2026   # 実測の保存先 data/user0/jfps2026/playback_<日付>/
for s in pm_rev pm_ch_F pm_ch_G; do
  python tools/run_signal_playback.py $s && python tools/check_pm_data.py $s; sleep 5
done
```
終わったらコンテナを出て、ホストで chown → `git add data/user0/jfps2026/playback_*/` → commit → push。

## `check_pm_data.py` の見方（2回目）
- `pm_rev`：2段目に対して 200 ms 後までに動いた割合。反転（rev）と継続（cont）の**平均**を見る。線形なら d・h によらずほぼ一定（O モデルの合成データで 0.8〜0.9）。小さい d・短い h で平均が下がれば、反転のしきい値
- `pm_ch_F` / `pm_ch_G`：そのチャネルの小ステップの t50、振幅比、反転の割合。DF（1回目）と比べる
