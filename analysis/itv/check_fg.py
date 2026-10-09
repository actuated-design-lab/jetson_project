"""check_fg.py — DF で同定した O / A / B＋漏れを、F・G チャネル（pm_ch_F / pm_ch_G, 10/6）にそのまま当てたときの当たり具合

  F・G は DF を一定に保って測っているので、時刻は DF の同期ステップのエコーで合わせ、同じずれを F・G の指令に使う。
  実機ログの物理 F = meas_pres_DF 列、物理 G = meas_pres_G 列。
  出力：NRMSE、小ステップの t50（指令から 50% 到達まで、ms）、正弦の振幅比
使い方: python analysis/itv/check_fg.py
"""
import os, sys, json, re, numpy as np, pandas as pd
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import pmeval as E
from base_eval import sim_O
from model_A import shape_cmd
from model_BL import sim_BL
TS = os.path.join(E.JP_DEFAULT, "signals") + "/"   # 入力信号（実測は E.find_measured で data/ から探す）
A = json.load(open(_HERE + "/fit_A.json")); BL = json.load(open(_HERE + "/fit_BL_w0.7.json")); O = np.array(A["O"])
fg, ag, G = np.array(A["fg"]), np.array(A["ag"]), np.array(A["G"])
thBL = [BL[k] for k in ["L","ki","kpp","d","km","cin","cout","ps","b","km_dn","kl"]]
sims = {"O": lambda u,p0: sim_O(u,p0,*O), "A": lambda u,p0: sim_O(shape_cmd(u,A["tc"],A["W"],A["hk"],A["h0"],fg,ag,G),p0,*O),
        "BL": lambda u,p0: sim_BL(u,p0,*thBL)}
def load(ch, fn):
    d = pd.read_csv(E.find_measured(fn, E.JP_DEFAULT)); sig = pd.read_csv(TS+f"pm_ch_{ch}_annotated.csv")
    fl = np.nan_to_num(d.flag.values.astype(float)); bad = np.abs(fl) > 1
    fl[bad] = np.interp(np.flatnonzero(bad), np.flatnonzero(~bad), fl[~bad])
    lag = E._echo_lag(fl, sig.cmd_pressure_DF.values)
    ix = np.clip(((np.arange(len(d)) - lag) * E.DT / 0.02).astype(int), 0, len(sig) - 1)
    u = sig["cmd_pressure_"+ch].values[ix]; seg = sig.segment.values[ix].astype(str)
    m = (d.meas_pres_DF if ch == "F" else d.meas_pres_G).values.astype(float)
    i0 = int(np.argmax(fl > 0.02)); return u[i0:], m[i0:], seg[i0:]
def t50(u, x, seg, pat):
    xs = np.convolve(x, np.ones(5)/5, "same"); out = {}
    for L in pd.unique(seg):
        mm = re.match(pat, L)
        if not mm: continue
        ii = np.flatnonzero(seg == L); on = ii[0]
        k = np.flatnonzero(np.abs(np.diff(u[on-20:on+20])) > 0.002); on = on-20+k[-1]+1 if len(k) else on
        d = u[on+5] - u[on-5]
        if abs(d) < 0.004: continue
        p0 = np.median(xs[on-30:on]); cr = np.flatnonzero((xs[on:on+200]-p0)*np.sign(d) >= 0.5*abs(d))
        out.setdefault(("up" if d > 0 else "dn", round(abs(d)*1000)), []).append(cr[0]*5 if len(cr) else np.nan)
    return {k: np.nanmedian(v) for k, v in sorted(out.items())}
def amps(u, x, seg):
    r = {}
    for L in pd.unique(seg):
        if not L.startswith("amp"): continue
        mm = re.search(r"a([\d.]+)_f([\d.]+)", L)
        if not mm: continue
        f = float(mm[2]); ii = np.flatnonzero(seg == L); ii = ii[int(len(ii)*0.3):]; tt = ii*E.DT
        r[(float(mm[1]), f)] = E.lock(x[ii], tt, f) / E.lock(u[ii], tt, f)
    return r
for ch, fn in (("F","data_pm_ch_F_1791270055.csv"), ("G","data_pm_ch_G_1791270328.csv")):
    u, m, seg = load(ch, fn); p0 = float(np.median(m[:20]))
    P = {k: s(u, p0) for k, s in sims.items()}
    print(f"\n=== {ch}  NRMSE%:", {k: round(E.nrmse(p, m), 2) for k, p in P.items()})
    print("segments:", sorted(set(re.sub(r"_r\d.*|_c[\d.]+.*", "", s) for s in pd.unique(seg)))[:20])
    pat = r"step_(up|dn|back|ret)"
    tr = t50(u, m, seg, pat); tb = t50(u, P["BL"], seg, pat); to = t50(u, P["O"], seg, pat)
    print("t50 ms  (dir,kPa): real / O / BL"); print({k: (tr[k], to.get(k), tb.get(k)) for k in tr})
    ar = amps(u, m, seg); ab = amps(u, P["BL"], seg); aa = amps(u, P["A"], seg)
    print("amp ratio (a,f): real / A / BL"); print({k: (round(ar[k],2), round(aa[k],2), round(ab[k],2)) for k in ar})
