# tools/pamtest/ — PAM 単体試験（等張性・等尺性）

目的：sim の力マップ（porcaro_2026 の `pam_force_map` / `pam_force_0_map`）を、カタログではなく実測で作り直す。
あわせて、PAM の摩擦（ヒステリシス）の向きが「圧力の変化」と「長さの変化」のどちらで決まるかを見る。

## 配線（2026/10 の試験機）

通信はデプロイ・信号再生と同じ（230400 bps、受信 7 要素、`src/microlabbox.py`）。Simulink 側の並びは変えていない。

| 受信の位置 | ロボットのとき | 試験機 |
|---|---|---|
| 0 | DF 内圧 | 試験する PAM の内圧（DF の圧力センサ）。`rig_config.yaml` の `channels.pressure: meas_pres_DF` のとき |
| 1 | F 内圧 | **レーザ変位計**（F の圧力センサの入力に挿した。値は圧力の換算を通った生の値 → `laser_calib.json` で mm に） |
| 2 | G 内圧 | 試験する PAM の内圧（G の圧力センサを DF のレギュレータの出口につないだ場合、2026/10/20〜）。`channels.pressure: meas_pres_G` のとき |
| 3, 4 | 手首角・グリップ角 | 使わない（記録だけ） |
| 5 | flag | MicroLabBox だけで使う（記録だけ） |
| 6 | 力 | **ロードセル**（これまでと同じ）。等尺性だけで使う。等張性は記録だけ |

送信は DF の ITV だけ使い、F・G には常に 0 MPa を送る。

## ファイル

| ファイル | 役割 |
|---|---|
| `rig.py` | 共通部品：送信（50 Hz）と安全監視、受信、非常停止キー、記録の保存、実機なしの疑似試験機 |
| `rig_config.yaml` | ポート、PAM の自然長 `L0_mm`、安全の上限（`limits`） |
| `laser_calib.json` | レーザの換算 `mm = gain * raw + offset` と収縮の向き `sign`（`check.py` が書く。試験機ごと） |
| `check.py` | 配線の確認、レーザの 2 点校正（`cal`）、向き（`dir`） |
| `isotonic.py` | 等張性（おもり一定）。計画は `tools/plans/pamtest_isotonic.yaml` |
| `isometric.py` | 等尺性（長さ固定）。長さを何段か変えて 1 セッションで回す。計画は `tools/plans/pamtest_isometric.yaml` |

## 実験の流れ（2026/10/20 にやった順。次もこの順で）

### 0. 準備
- 治具（3D プリント、PLA）：ねじは通し穴＋ナット埋め込み（PLA に直接タップしない）。力が層に沿う向きに積層する。
  等尺性ではロードセルの固定部に数百 N かかる（DF、収縮 0 %・0.6 MPa で約 290 N）
- おもり：100 g × 2、200 g × 2、500 g × 3、1 kg × 1（0.1〜3.1 kg を 0.1 kg 刻みで作れる）
- 供給圧：0.6 MPa 付近（ITV は供給より上は出せない）

### 1. MicroLabBox（Simulink / ControlDesk）
- 配線は上の表。**10/20 は DF の圧力センサが読めなかった**ので、G の圧力センサを DF のレギュレータの出口につなぎ、
  `rig_config.yaml` を `channels.pressure: meas_pres_G` にした。DF のセンサを直したら `meas_pres_DF` に戻す
- ロードセル：アンプ DPM-911B（VOLTAGE OUT 10.00 V = 1636 µε → 10 V = 500 N）、Simulink は ×10（ADC → V）→ ×50（N/V）。
  **Simulink の `fcn`（5 サンプルのピークホールド、初期値 −100）が、引っ張り（マイナスの値）を −100 N で切る**ので、
  試験機では `Force [N]` の手前の `-1` を `1` にして、引っ張りを + にする。**ロボットに戻すときは必ず `-1` に戻す**
  （打撃力の符号が逆になる）
- ControlDesk：実験が Running、出力の許可（flag）が ON。ここが OFF だとレギュレータが動かない

### 2. Jetson
```bash
# 時刻合わせ・FTDI・コンテナ起動はリポジトリの README のとおり
git pull --no-rebase --no-edit
export PORCARO_USER=user0 PORCARO_VENUE=pamtest      # venue を jfps2026 などと間違えない
```

### 3. 配線確認とレーザ（`check.py`）
- `m`：受信 200 Hz、内圧・レーザ・力が動くか（力は手で軽く引いて確かめる）
- `p 0.6` → `p 0`：筋が縮みきった位置と戻った位置で、レーザのアンプに「---」が出ないか（近すぎると範囲外で値が止まる）
- `cal`：raw がすでに mm で出ているなら省略できる。`p 0` と `p 0.6` の差をノギスで測って raw の差と合えば省略
  （10/20 は省略、gain 1）。合わなければ 2 点校正
- `dir`：おもり無し・手を離して。縮むと + になる向きを決める（10/20 は sign = −1）
- 10/20 の目安：0.2 MPa・おもり無しで約 4.7 mm（3.1 %）縮む

### 4. 等張性（約 30 分＋付け替え）
```bash
python3 tools/pamtest/isotonic.py --pam DF_robot
```
- 最初の質問：おもり無しのまま Enter（長さの基準）
- おもり：0 → 0.2 → 0.5 → 1.0 → 1.5 → 2.0 → 3.1 kg を足して、2.0 → … → 0 と外す（13 回）。1 段ごとに 0→0.6→0 MPa を自動で 1 往復（約 2 分）
- 1 段目（0 kg）の 0.6 MPa で収縮が +18 % 前後なら正常
- 止まったら（下の「トラブル」）、その段から再開：
  ```bash
  python3 tools/pamtest/isotonic.py --pam DF_robot --start_kg 1.5 [--start_dir remove] --ref_from <止まったランの .json>
  ```

### 5. 等尺性（約 40 分）
```bash
python3 tools/pamtest/isometric.py --pam DF_robot
```
- 長さは 0 → 2 → … → 14 %（筋は縮む向きにしか手で動かせないので小さい順）
- 各長さ：治具をゆるめて Enter → 圧力で自動で縮める → 必要なら + / − で微調整 → **治具を固定して Enter** → 0 MPa → 3 往復
- 固定して 0 MPa に抜くと、筋がたるんで治具のがたの分だけ戻る。これは正常（`slack_mm` に残る）。
  実際の収縮率は固定したとき（加圧した状態）の位置から出る。★（設定から 0.4 % 以上ずれ）が出たら q で止めて固定し直す
- 止まったら、自然長の基準を引き継いでその長さから再開（`--ref_from` は自然長を取った最初のセッションの .json）：
  ```bash
  python3 tools/pamtest/isometric.py --pam DF_robot --start_pct 12 --ref_from <最初のセッションの .json>
  ```

### 6. 終わったら
```bash
# ホスト側（コンテナの外）で
git add data/user0/pamtest tools/pamtest/laser_calib.json
git commit        # 無効なラン（止まった・固定ミスなど）と理由、再開したランの対応を書く
git push
```
- 全記録は `.csv.gz`。無圧縮だと 30 分で 100 MB を超えて push できない（古い .csv は `compress_logs.py --apply`）
- 解析：`python3 analysis/pamtest/force_map.py --porcaro ../porcaro_2026`（使うランと段はスクリプト冒頭の `ISOM` / `ISOT` に書く）
  → `data/user0/pamtest/summary/`。sim のマップは porcaro_2026 の `analysis/eval/make_measured_map.py`

### トラブルと対処（10/20 に起きたこと）

| 症状 | 原因 | 対処 |
|---|---|---|
| レギュレータが動かない（最初） | ControlDesk の出力の許可（flag）が OFF | 許可を ON |
| 10〜15 分ごとにレギュレータが指令を受け付けなくなる | MicroLabBox 側（原因は未特定。受信は続いていたので非常停止はかからない） | ビルドし直して `--start_*` で再開。数段ごとにビルドし直すと安全。次に起きたら、ビルドの前に ControlDesk で受信した指令（cmd_DF）が変わるか見る |
| 内圧が 0 のまま | DF の圧力センサが故障 | G のセンサで代用（`channels.pressure`） |
| 力が −100 N で頭打ち | Simulink の `fcn` の初期値 −100 | 符号を反転（上の 1） |
| 等尺性で 2 mm 動いて止まる | 治具の伸び・固定ミス | 治具の隙間を埋める、固定し直す |
| push できない（100 MB 超） | 無圧縮の CSV | `.csv.gz`（今は自動）、古いものは `compress_logs.py` |
| `git pull` が divergent branches で止まる | Jetson でコミットした後に pull | `git pull --no-rebase --no-edit` |

### 10/20 の結果
`data/user0/pamtest/summary/summary.md`、`fig_force_length.png`、`fig_length_hysteresis.png`。
実測の力はカタログの 0.3〜0.7 倍（中央値 0.58）、自由収縮は 0.6 MPa で 18.4 %。sim 用のマップは porcaro_2026 の
`tasks/data/pam_force_map_measured.csv`（使い方は同じフォルダの README）。

## コマンド早見

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

# 2. 等尺性：自然長の基準 → 長さごとに「ゆるめる → 圧力で縮める → 固定 → 0 MPa → 測る」
python3 tools/pamtest/isometric.py --pam DF_robot                         # plan の 0, 2, …, 14 %
python3 tools/pamtest/isometric.py --pam DF_robot --contractions 0 4 8    # 長さを指定
python3 tools/pamtest/isometric.py --pam DF_robot --start_pct 6 --ref_from <前のセッションの .json>   # 途中から再開

# 実機なしで流れだけ確かめる
python3 tools/pamtest/isotonic.py --pam test --mock
```

付け替えの合図（等張性）：Enter ＝ 表示どおりのおもりにした ／ 数値 ＝ 実際に掛けた kg ／ s ＝ この段を飛ばす ／ q ＝ 終わる。

## 安全


- 自動で動いている間は **スペース / e / q / Esc** のどれかで即 0 MPa（非常停止）。Ctrl+C も同じ
- 自動停止（`rig_config.yaml` の `limits`）：内圧 > `p_meas_max`、|力| > `force_max_N`（等尺性だけ）、受信の途絶 > `rx_timeout_s`、
  等尺性の測定中に変位が固定位置から > `isometric_move_mm`（治具のずれ・破損）
- 等尺性で長さを変えるとき（治具をゆるめて圧力で縮め、固定するまで）は加圧したまま人が治具を触る。手を挟まないように
- 指令は `p_cmd_max` でクリップ。どの終わり方でも 0 MPa を送ってから閉じる
- ソフトの停止は ITV を 0 MPa にするだけ。Jetson が固まったときに備えて、供給側の手元バルブを手の届くところに
- 等張性の付け替え待ち（Enter 待ち）の間はキーの非常停止は効かない（Ctrl+C と自動停止は効く）
- `pressure_major` は加圧したままおもりを替える。手を挟まないように

## 出力

`data/<user>/<venue>/<種類>_<日付>/`（種類 = `isotonic` / `isometric` / `rigcheck`）

- `<名前>.csv.gz`：200 Hz の全記録（gzip、有効数字 7 桁。`pandas.read_csv` でそのまま読める）。`cmd_DF, meas_pres_DF, laser_raw, laser_mm, force_N, flag` に、
  その時点の段の情報（`phase, load_kg, P_set, P_dir, cycle, step`）が付く
- `<名前>_steps.csv`：各段の保持の最後 `avg_s` 秒の平均（等張性は収縮率、等尺性は 0 点を引いた力）
- `<名前>.json`：計画・設定・レーザの換算と基準・非常停止の理由・受信率・イベント（基準を取った時刻など）

等張性はおもり 1 段（`pressure_major` は圧力 1 本）ごと、等尺性は長さ 1 つごとに途中保存する。
等尺性の `_steps.csv` には設定の収縮率 `c_set` と、自然長の基準から測った実際の収縮率 `c_actual` が入る。
