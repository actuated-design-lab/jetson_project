# models/BINARY/ — 電磁弁（2値）版モデル置き場

sim 側 `actuated-design-lab/porcaro_2026` のタスク `Porcaro-DR-Discrete-user0` で学習したモデルを、
ONNX に export してここに置く。

## 2値化はどこでやるか

- ONNX は **2値化する前の連続値の action**（-1〜1）を出す。sim でも、2値化は方策ではなくコントローラ
  （`common/actions/discrete_torque.py`）が圧力指令に対して行っている。
- 実機では `models/manifest.yaml` の `action_mode: binary` に従って `src/model_registry.py` が2値化する:
  `p = (clip(a)+1)/2 * p_max` が `binary_threshold * p_max` 以上なら `p_max`、未満なら `0`。
- **`binary_threshold` は sim の `controller.discrete_threshold`（既定 0.5）と同じ値にすること。**
  学習したランの `params/env.yaml` で確認できる。
- 次の観測に入れる `prev_action` は、sim と同じく clip 後の連続値（2値化前）。

## export（sim 側リポジトリで）

```bash
python scripts/rsl_rl/export_onnx.py \
    --task Porcaro-DR-Discrete-user0 \
    --agent rsl_rl_lstm_cfg_entry_point \
    --checkpoint=logs/user0/rsl_rl/porcaro_rslrl_lstm_dr_discrete/<ラン>/model_1499.pt \
    --lookahead_horizon 0.5 \
    --out_dir <出力先> --out_name binary_scratch_seed1
```

正規化の焼き込み・LSTM の入出力数など、守ることは `models/RAL/README.md` の「export で必ず守ること」と同じ。

## ファイル名

`binary_<学習条件>_seed<N>.onnx`。例:
- `binary_scratch_seed1.onnx` … 2値で最初から学習
- `binary_ft_from_B_seed1.onnx` … RA-L の連続値 Model B（seed1）から2値で追加学習

## 置いたあとの手順

```bash
# 1. models/manifest.yaml の BINARY: セクションのコメントを外して登録（action_mode: binary を忘れない）
# 2. 整合性チェック
python3 tools/check_models.py
# 3. 実機なしで、指令が 0 / 0.6 MPa だけになっているか確認（最後に [Check] ... OK と出る）
python3 src/deploy_policy.py --model BINARY/scratch_seed1 --midi songs/test_single4_bpm60.mid --mock
```

出力は `data/binary_<日付>/` に出る。`.json` の `action_mode` と `cmd_binary_ok` で、
2値で動かしたことと、実際に送った指令が 0 / p_max だけだったことを後から確認できる。
