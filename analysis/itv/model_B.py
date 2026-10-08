"""モデルB：パイロット段（積分＋比例）と、重なり（不感帯）を持つ主弁の2状態モデル

  e   = u(t-L) - p                       ITV 内部の圧力センサとの偏差
  pi' = ki * e                           パイロット室：給気・排気の電磁弁で偏差を積分
  pp  = pi + kpp * e                     パイロット圧（比例分も含める）
  x   = pp - p                           ダイアフラムにかかる差
  主弁：x > d なら給気、x < -d なら排気、|x| <= d は閉（重なり＝中立点付近の高抵抗）
  p'  = km * (x ∓ d)  を、給気・排気のオリフィス流量の上限で頭打ち（流量上限モデル O と同じ式）

使い方: python model_B.py
"""
import os, sys, json, time
import numpy as np, pandas as pd
from numba import njit
from scipy.optimize import least_squares
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import pmeval as E, fit_common as F
from base_eval import _g, sim_O

DT = 0.005; PA = 0.1013
NAMES = ["L", "ki", "kpp", "d", "km", "cin", "cout", "ps", "b", "km_dn"]


@njit(cache=False)
def sim_B(cmd, p0, L, ki, kpp, d, km, cin, cout, ps, b, km_dn):
    Df = L / DT; M = int(np.floor(Df)); mu = Df - M; p = p0; pi = p0; out = np.empty(len(cmd))
    for i in range(len(cmd)):
        a = cmd[i - M] if i - M >= 0 else cmd[0]
        bb = cmd[i - M - 1] if i - M - 1 >= 0 else cmd[0]
        u = (1 - mu) * a + mu * bb
        e = u - p
        pi += ki * e * DT
        if pi < 0.0: pi = 0.0
        if pi > ps: pi = ps
        x = pi + kpp * e - p
        if x > d:
            q = km * (x - d); pab = p + PA
            lim = cin * _g(ps + PA, pab, b) if ps + PA > pab else 0.0
            q = min(q, lim)
        elif x < -d:
            q = km_dn * (x + d); pab = p + PA
            lim = cout * _g(pab, PA, b) if pab > PA else 0.0
            q = max(q, -lim)
        else:
            q = 0.0
        p += q * DT; out[i] = p
    return out


if __name__ == "__main__":
    D = E.load_all(); M = F.prep_map(D)
    res = F.make_residual(D, M, lambda th: (lambda u, p0: sim_B(u, p0, *th)), w_map=float(sys.argv[1]) if len(sys.argv) > 1 else 1.0)
    lo = [0.0, 1.0, 0.0, 0.0, 1.0, 0.1, 0.1, 0.45, 0.05, 1.0]
    hi = [0.1, 400., 5.0, 0.08, 300., 60., 60., 0.9, 0.6, 300.]
    rng = np.random.default_rng(0)
    inits = [[0.025, 11, 0.07, 0.015, 28, 3.8, 20, 0.587, 0.05, 24]]
    for _ in range(4):
        inits.append([rng.uniform(0.02, 0.06), np.exp(rng.uniform(np.log(5), np.log(60))), rng.uniform(0, 0.6), rng.uniform(0.003, 0.03),
                      np.exp(rng.uniform(np.log(8), np.log(80))), rng.uniform(2, 8), rng.uniform(5, 40), rng.uniform(0.5, 0.65), rng.uniform(0.05, 0.4),
                      np.exp(rng.uniform(np.log(8), np.log(80)))])
    best = None
    for x0 in inits:
        t = time.time()
        r = least_squares(res, x0, bounds=(lo, hi), diff_step=0.02, max_nfev=3000, ftol=1e-12, xtol=1e-12, gtol=1e-12, x_scale='jac')
        print(f"cost {r.cost:.5f} nfev {r.nfev} ({time.time()-t:.0f}s)", dict(zip(NAMES, np.round(r.x, 4))), flush=True)
        if best is None or r.cost < best.cost:
            best = r
    th = best.x
    json.dump(dict(zip(NAMES, map(float, th))), open(_HERE + f"/fit_B2_w{sys.argv[1] if len(sys.argv)>1 else 1}.json", "w"), indent=1)
    rr = E.evaluate(D, lambda u, p0: sim_B(u, p0, *th))
    O = np.array(json.load(open(_HERE + "/struct_vs_data_fits.json"))["step"]["O"])
    ro = E.evaluate(D, lambda u, p0: sim_O(u, p0, *O))
    print(pd.DataFrame([E.summary(ro, "O"), E.summary(rr, "B")]).to_string())
    print("t50 real ", {k: round(v) for k, v in sorted(rr["t50_real"].items())})
    print("t50 B    ", {k: round(v) for k, v in sorted(rr["t50_model"].items())})
    T = rr["map"]; print(T[(T.c == 0.3) & T.data.isin(["ampsweep", "sine2"])].round(2).to_string())
