# tools/pamtest/ — PAM 単体試験（等張性・等尺性）

目的：sim の力マップ（porcaro_2026 の `pam_force_map` / `pam_force_0_map`）を、カタログではなく実測で作り直す。
あわせて、PAM の摩擦（ヒステリシス）の向きが「圧力の変化」と「長さの変化」のどちらで決まるかを見る。

## 配線（2026/10 の試験機）

通信はデプロイ・信号再生と同じ（230400 bps、受信 7 要素、`src/microlabbox.py`）。Simulink 側の並びは変えていない。

| 受信の位置 | ロボットのとき | 試験機 |
|---|---|---|
| 0 | DF 内圧 | **試験する PAM の内圧**（DF の圧力センサ） |
| 1 | F 内圧 | **レーザ変位計**（F の圧力センサの入力に挿した。値は圧力の換算を通った生の値 → `laser_calib.json` で mm に） |
| 2 | G 内圧 | 使わない（記録だけ） |
| 3, 4 | 手首角・グリップ角 | 使わない（記録だけ） |
| 5 | flag | MicroLabBox だけで使う（記録だけ） |
| 6 | 力 | **ロードセル**（これまでと同じ） |

送信は DF の ITV だけ使い、F・G には常に 0 MPa を送る。

## ファイル

| ファイル | 役割 |
|---|---|
| `rig.py` | 共通部品：送信（50 Hz）と安全監視、受信、非常停止キー、記録の保存、実機なしの疑似試験機 |
| `rig_config.yaml` | ポート、PAM の自然長 `L0_mm`、安全の上限（`limits`） |
| `laser_calib.json` | レーザの換算 `mm = gain * raw + offset` と収縮の向き `sign`（`check.py` が書く。試験機ごと） |
| `check.py` | 配線の確認、レーザの 2 点校正（`cal`）、向き（`dir`） |
| `isotonic.py` | 等張性（おもり一定）。計画は `tools/plans/pamtest_isotonic.yaml` |
| `isometric.py` | 等尺性（長さ固定）。計画は `tools/plans/pamtest_isometric.yaml` |

## 手順

```bash
export PORCARO_USER=user0 PORCARO_VENUE=pamtest

# 0. 配線確認とレーザの校正（試験機を組み直したら毎回）
python3 tools/pamtest/check.py
#   m      → 内圧・レーザ・力が動いているか、受信 200 Hz か
#   cal    → ターゲットを 2 か所に置いて位置 [mm] を入力
#   dir    → 軽い荷重で 0.2 MPa をかけ、縮むと + になる向きを決める
#   p 0.3  → 手で圧力をかけて確認、p 0 で抜く

# 1. 等張性：おもりの付け替えのたびに Enter を待つ
python3 tools/pamtest/isotonic.py --pam DF_robot                        # おもり 1 段ごとに圧力の階段
python3 tools/pamtest/isotonic.py --pam DF_robot --mode pressure_major  # 圧力固定でおもりを足し引き

# 2. 等尺性：治具の長さを変えるたびに 1 ラン
python3 tools/pamtest/isometric.py --pam DF_robot --contraction_pct 0
python3 tools/pamtest/isometric.py --pam DF_robot --contraction_pct 4 --cycles 0   # 止めるまで

# 実機なしで流れだけ確かめる
python3 tools/pamtest/isotonic.py --pam test --mock
```

付け替えの合図（等張性）：Enter ＝ 表示どおりのおもりにした ／ 数値 ＝ 実際に掛けた kg ／ s ＝ この段を飛ばす ／ q ＝ 終わる。

## 安全

- 自動で動いている間は **スペース / e / q / Esc** のどれかで即 0 MPa（非常停止）。Ctrl+C も同じ
- 自動停止（`rig_config.yaml` の `limits`）：内圧 > `p_meas_max`、|力| > `force_max_N`、受信の途絶 > `rx_timeout_s`、
  等尺性で変位が基準から > `isometric_move_mm`（治具のずれ・破損）
- 指令は `p_cmd_max` でクリップ。どの終わり方でも 0 MPa を送ってから閉じる
- ソフトの停止は ITV を 0 MPa にするだけ。Jetson が固まったときに備えて、供給側の手元バルブを手の届くところに
- 等張性の付け替え待ち（Enter 待ち）の間はキーの非常停止は効かない（Ctrl+C と自動停止は効く）
- `pressure_major` は加圧したままおもりを替える。手を挟まないように

## 出力

`data/<user>/<venue>/<種類>_<日付>/`（種類 = `isotonic` / `isometric` / `rigcheck`）

- `<名前>.csv`：200 Hz の全記録。`cmd_DF, meas_pres_DF, laser_raw, laser_mm, force_N, flag` に、
  その時点の段の情報（`phase, load_kg, P_set, P_dir, cycle, step`）が付く
- `<名前>_steps.csv`：各段の保持の最後 `avg_s` 秒の平均（等張性は収縮率、等尺性は 0 点を引いた力）
- `<名前>.json`：計画・設定・レーザの換算と基準・非常停止の理由・受信率・イベント（基準を取った時刻など）

等張性はおもり 1 段（`pressure_major` は圧力 1 本）ごと、等尺性は 1 往復ごとに途中保存する。
