"""force_map.py — PAM 単体試験（10/20、予備の筋。ファイル名の DF_robot は誤り）の等尺性・等張性から、力–収縮率–圧力の関係を描き、カタログ・今の sim と比べる

入力（data/user0/pamtest/）
  等尺性 isometric_20261020/：有効な長さだけ（ISOM）。各段の収縮率はレーザの位置（自然長の基準 natural_ref_mm から）で出す
    （固定したときの位置ではなく段ごとの実測。力がほぼ 0 の低い圧力では筋が治具から離れて縮みきっていない）
    力は 0 MPa で取った 0 点を引いた値（force_N_zeroed）
  等張性 isotonic_20261020/：有効な段だけ（ISOT）。力はおもりの重さ（吊り具 0 kg 扱い）、収縮率は contraction_pct
  カタログ：porcaro_2026 の tasks/data/pam_force_map.csv（DMSP-10-100N の読み取り。行 = 圧力 MPa、列 = 収縮率 %）
    今の sim はこれ × force_scale 0.2（user0/cfg/controller_cfg.py）

出力（data/user0/pamtest/summary/）
  fig_force_length.png：圧力ごとの力–収縮率（実測の上り・下り、カタログ、今の sim）
  fig_length_hysteresis.png：等張性のおもりごとの収縮率–圧力（上げ・下げ）
  isometric_table.csv：等尺性の長さ × 圧力 × 上り下り（3 往復の平均）
  isotonic_table.csv：等張性のおもり × 足す/外す × 圧力 × 上げ/下げ
  summary.md：数字のまとめ

  python3 analysis/pamtest/force_map.py --porcaro ../porcaro_2026
"""
from __future__ import annotations

import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.interpolate import RegularGridInterpolator  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
D = os.path.join(REPO, "data", "user0", "pamtest")
L0 = 150.0
G = 9.80665

# 有効な長さ（コミット d1d67b3 の説明どおり）。1792470451 / 1792471109 / 1792471160 / 1792471312 は 0 % の取り直し前、
# 1792471553 の 4 % は治具の固定ミス、1792472882 は 4 % のやり直し前（変位が飛んだ）、1792473283 の 12 % は途中で中断
ISOM = [("isometric_DF_robot_1792471553", [0.0, 2.0]),
        ("isometric_DF_robot_1792473283", [4.0, 6.0, 8.0, 10.0]),
        ("isometric_DF_robot_1792475043", [12.0, 14.0])]
# 等張性：1792462015 は 1 kg でレギュレータが止まった 1 回目（使わない）。再開の前後で重なる途中の段は落とす
ISOT = [("isotonic_DF_robot_load_major_1792465672", ("drop", 1.5, "add")),
        ("isotonic_DF_robot_load_major_1792466718", ("drop", 1.0, "remove")),
        ("isotonic_DF_robot_load_major_1792467697", None)]

C_MEAS, C_SIM, C_CAT, INK2, MUTED, GRID = "#2a78d6", "#eb6834", "#898781", "#52514e", "#898781", "#e1e0d9"


def load_isometric() -> pd.DataFrame:
    out = []
    for stem, cs in ISOM:
        m = json.load(open(os.path.join(D, "isometric_20261020", stem + ".json"), encoding="utf-8"))
        d = pd.read_csv(os.path.join(D, "isometric_20261020", stem + "_steps.csv"))
        d = d[d.c_set.isin(cs)].copy()
        d["h"] = m["laser_calib"]["sign"] * (d.laser_mm - m["natural_ref_mm"]) / L0 * 100
        d["F"] = d.force_N_zeroed
        d["run"] = stem
        out.append(d)
    return pd.concat(out, ignore_index=True)


def load_isotonic() -> pd.DataFrame:
    out = []
    for stem, drop in ISOT:
        d = pd.read_csv(os.path.join(D, "isotonic_20261020", stem + "_steps.csv"))
        if drop:
            d = d[~((d.load_kg == drop[1]) & (d.load_dir == drop[2]))]
        d = d.copy()
        d["h"], d["F"], d["run"] = d.contraction_pct, d.total_load_N, stem
        out.append(d)
    return pd.concat(out, ignore_index=True)


def catalog(porcaro: str):
    fm = pd.read_csv(os.path.join(porcaro, "source/porcaro_2026/porcaro_2026/tasks/data/pam_force_map.csv"), index_col=0)
    f = RegularGridInterpolator((fm.index.values.astype(float), fm.columns.values.astype(float)), fm.values,
                                bounds_error=False, fill_value=None)
    return lambda P, h: f(np.c_[np.broadcast_to(P, np.shape(h)), h])


def style(ax):
    ax.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=INK2, labelsize=8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--porcaro", default=os.environ.get("PORCARO_REPO", os.path.join(os.path.dirname(REPO), "porcaro_2026")))
    a = ap.parse_args()
    cat = catalog(a.porcaro)
    out = os.path.join(D, "summary")
    os.makedirs(out, exist_ok=True)
    iso, ton = load_isometric(), load_isotonic()

    # 等尺性：3 往復の平均
    gi = iso.groupby(["c_set", "P_cmd", "P_dir"]).agg(P=("P_meas", "mean"), h=("h", "mean"), F=("F", "mean"),
                                                       F_sd=("F", "std"), n=("F", "size")).reset_index()
    gi.round(4).to_csv(os.path.join(out, "isometric_table.csv"), index=False)
    gt = ton.groupby(["load_kg", "load_dir", "P_cmd", "P_dir"]).agg(P=("P_meas", "mean"), h=("h", "mean"),
                                                                     F=("F", "mean")).reset_index()
    gt.round(4).to_csv(os.path.join(out, "isotonic_table.csv"), index=False)

    # ---- 図 1：圧力ごとの力–収縮率 ------------------------------------------------
    Ps = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    fig, axs = plt.subplots(2, 3, figsize=(11, 6.4), sharex=True, sharey=True, facecolor="#fcfcfb")
    hh = np.linspace(-1, 25, 261)
    for ax, P in zip(axs.flat, Ps):
        ax.set_facecolor("#fcfcfb")
        style(ax)
        Pm = gi[gi.P_cmd == P].P.mean()
        ax.plot(hh, cat(Pm, hh), color=C_CAT, lw=1.5, ls="--", label="catalog (DMSP-10-100N)")
        ax.plot(hh, 0.2 * cat(Pm, hh), color=C_SIM, lw=2, label="current sim (catalog × 0.2)")
        for src, g, mk in (("isometric", gi, "o"), ("isotonic", gt, "^")):
            s = g[g.P_cmd == P]
            for d, fill in (("up", True), ("down", False)):
                q = s[s.P_dir == d]
                ax.plot(q.h, q.F, mk, ms=6, mfc=C_MEAS if fill else "#fcfcfb", mec=C_MEAS, mew=1.3, ls="none",
                        label=f"measured {src}, pressure {'rising' if d == 'up' else 'falling'}")
        ax.set_title(f"{P:.1f} MPa (measured {Pm:.3f})", fontsize=9, color=INK2, loc="left")
        ax.set_xlim(-1, 25)
        ax.set_ylim(-10, 520)
    for ax in axs[1]:
        ax.set_xlabel("contraction h [%]  (L0 = 150 mm)", fontsize=9, color=INK2)
    for ax in axs[:, 0]:
        ax.set_ylabel("force [N]", fontsize=9, color=INK2)
    h_, l_ = axs[0, 0].get_legend_handles_labels()
    fig.legend(h_, l_, loc="lower center", ncol=3, fontsize=8, frameon=False, labelcolor=INK2)
    fig.suptitle("Spare PAM, single-unit test 2026-10-20: force vs contraction", fontsize=11, color="#0b0b0b",
                 x=0.06, ha="left")
    fig.tight_layout(rect=(0, 0.08, 1, 0.97))
    fig.savefig(os.path.join(out, "fig_force_length.png"), dpi=160)
    plt.close(fig)

    # ---- 図 2：等張性の収縮率–圧力（長さのヒステリシス） ----------------------------
    fig, axs = plt.subplots(1, 3, figsize=(11, 3.6), sharey=True, facecolor="#fcfcfb")
    for ax, (w, wd) in zip(axs, ((0.0, "add"), (1.0, "add"), (3.1, "add"))):
        ax.set_facecolor("#fcfcfb")
        style(ax)
        s = ton[(ton.load_kg == w) & (ton.load_dir == wd)].sort_values("step")
        ax.plot(s.P_meas, s.h, color=C_MEAS, lw=1.2, alpha=0.6)
        for d, fill in (("up", True), ("down", False)):
            q = s[s.P_dir == d]
            ax.plot(q.P_meas, q.h, "o", ms=6, mfc=C_MEAS if fill else "#fcfcfb", mec=C_MEAS, mew=1.3, ls="none",
                    label=f"pressure {'rising' if d == 'up' else 'falling'}")
        ax.set_title(f"load {w:g} kg ({w * G:.1f} N)", fontsize=9, color=INK2, loc="left")
        ax.set_xlabel("pressure [MPa]", fontsize=9, color=INK2)
    axs[0].set_ylabel("contraction h [%]", fontsize=9, color=INK2)
    axs[0].legend(fontsize=8, frameon=False, labelcolor=INK2)
    fig.suptitle("Isotonic: contraction vs pressure (one up-down cycle per load)", fontsize=11, color="#0b0b0b",
                 x=0.06, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(os.path.join(out, "fig_length_hysteresis.png"), dpi=160)
    plt.close(fig)

    # ---- 数字 -----------------------------------------------------------------------
    L = ["# PAM 単体試験 10/20（予備の筋。ファイル名の DF_robot は誤り）のまとめ（analysis/pamtest/force_map.py）", ""]
    up = gi[gi.P_dir == "up"].copy()
    up["cat"] = [float(cat(p, np.array([h]))[0]) for p, h in zip(up.P, up.h)]
    up["ratio"] = up.F / up.cat
    loaded = up[up.F > 10]
    L += ["## 等尺性（圧力を上げる側、3 往復の平均）の力 [N]", "", "```",
          up.pivot_table(index="P_cmd", columns="c_set", values="F").round(1).to_string(), "```", "",
          "## そのときの収縮率 [%]（段ごとのレーザ位置。力がほぼ 0 の段は治具に届いていない）", "", "```",
          up.pivot_table(index="P_cmd", columns="c_set", values="h").round(2).to_string(), "```", "",
          "## 実測 ÷ カタログ（力 > 10 N の点、カタログは実測の圧力・収縮率で補間）", "", "```",
          loaded.pivot_table(index="P_cmd", columns="c_set", values="ratio").round(2).to_string(), "```", "",
          f"- 全体：中央値 {loaded.ratio.median():.2f}（範囲 {loaded.ratio.min():.2f}〜{loaded.ratio.max():.2f}、"
          f"{len(loaded)} 点）。今の sim は 0.2", ""]
    # 力のヒステリシス（同じ長さ・同じ圧力で、下げ − 上げ）
    pv = gi.pivot_table(index=["c_set", "P_cmd"], columns="P_dir", values="F").dropna()
    pv = pv[(pv.up > 10) & (pv.down > 10)]
    hy = (pv.down - pv.up).rename("dF").reset_index()
    L += ["## 力のヒステリシス：同じ長さ・圧力での 下げ − 上げ [N]（両方 10 N 超の点）", "", "```",
          hy.pivot_table(index="P_cmd", columns="c_set", values="dF").round(1).to_string(), "```", "",
          "圧力ごとの中央値: " + ", ".join(f"{p:.1f} MPa {v:.1f} N" for p, v in hy.groupby("P_cmd").dF.median().items()), ""]
    # 等張性
    t0 = gt[(gt.load_kg == 0.0) & (gt.load_dir == "add")].pivot_table(index="P_cmd", columns="P_dir", values="h")
    L += ["## 等張性：収縮率 [%]（おもり × 足す/外す、圧力の上げ/下げ）", "", "```",
          gt.pivot_table(index=["P_cmd", "P_dir"], columns=["load_kg", "load_dir"], values="h").round(2).to_string(),
          "```", "", "## 自由収縮（おもり 0 kg、最初の 0 kg）と長さのヒステリシス", "", "```",
          t0.assign(down_minus_up=t0["down"] - t0["up"]).round(2).to_string(), "```", ""]
    # 等張性と等尺性の突き合わせ（0.5・0.6 MPa の上り）
    chk = []
    for P in (0.5, 0.6):
        s = up[up.P_cmd == P].sort_values("h")
        for w in (2.0, 3.1):
            t = gt[(gt.load_kg == w) & (gt.load_dir == "add") & (gt.P_cmd == P) & (gt.P_dir == "up")]
            if len(t):
                h = float(t.h.iloc[0])
                if h > s.h.max():
                    chk.append(f"- {P} MPa・{w} kg（{w * G:.1f} N）：等張性の収縮 {h:.2f} % は等尺性の範囲"
                               f"（〜{s.h.max():.2f} %）の外")
                    continue
                chk.append(f"- {P} MPa・{w} kg（{w * G:.1f} N）：等張性の収縮 {h:.2f} % → 等尺性を内挿した力 "
                           f"{np.interp(h, s.h, s.F):.1f} N")
    L += ["## 等張性と等尺性の突き合わせ（同じ圧力・収縮率での力）", ""] + chk + [""]
    # 長さのヒステリシスを圧力の幅で：上げで h になった圧力と、下げで同じ h になる圧力の差
    wl = []
    for w in (0.0, 1.0, 3.1):
        s = gt[(gt.load_kg == w) & (gt.load_dir == "add")]
        u, dn = s[s.P_dir == "up"].sort_values("P"), s[s.P_dir == "down"].sort_values("P")
        dn = pd.concat([dn, u[u.P_cmd == u.P_cmd.max()]]).sort_values("P")   # 下げは最上段（上げの最後）から始まる
        for _, r in u[(u.P_cmd >= 0.2) & (u.P_cmd <= 0.5)].iterrows():
            if dn.h.min() <= r.h <= dn.h.max():
                wl.append({"load_kg": w, "P_up": r.P_cmd, "h": r.h, "dP_kPa": (r.P - np.interp(r.h, dn.h, dn.P)) * 1000})
    wl = pd.DataFrame(wl)
    L += ["## 長さのヒステリシスを圧力の幅で（等張性、上げで h になった圧力 − 下げで同じ h になる圧力）", "", "```",
          wl.pivot_table(index="P_up", columns="load_kg", values="dP_kPa").round(0).to_string(), "```", ""]
    open(os.path.join(out, "summary.md"), "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
