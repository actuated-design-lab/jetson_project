"""モデルB＋漏れ：主弁の重なり（|x|<=d）の中でも、x に比例した小さな流れ（漏れ）を許す
   |x| <= d : p' = kl·x
   x  >  d  : p' = kl·d + km·(x - d)
   x  < -d  : p' = -kl·d + km_dn·(x + d)        （どれも給気・排気の流量上限で頭打ち）
使い方: python model_BL.py [w_map]"""
import os, sys, json, time
import numpy as np, pandas as pd
from numba import njit
from scipy.optimize import least_squares
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import pmeval as E, fit_common as F
from base_eval import _g, sim_O
from model_B import sim_B

DT = 0.005; PA = 0.1013
NAMES = ["L", "ki", "kpp", "d", "km", "cin", "cout", "ps", "b", "km_dn", "kl"]


@njit(cache=False)
def sim_BL(cmd, p0, L, ki, kpp, d, km, cin, cout, ps, b, km_dn, kl):
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
            q = kl * d + km * (x - d)
        elif x < -d:
            q = -kl * d + km_dn * (x + d)
        else:
            q = kl * x
        pab = p + PA
        if q > 0:
            lim = cin * _g(ps + PA, pab, b) if ps + PA > pab else 0.0
            q = min(q, lim)
        else:
            lim = cout * _g(pab, PA, b) if pab > PA else 0.0
            q = max(q, -lim)
        p += q * DT; out[i] = p
    return out


if __name__ == "__main__":
    w = float(sys.argv[1]) if len(sys.argv) > 1 else 0.7
    D = E.load_all(); M = F.prep_map(D)
    res = F.make_residual(D, M, lambda th: (lambda u, p0: sim_BL(u, p0, *th)), w_map=w)
    lo = [0.0, 1.0, 0.0, 0.0, 1.0, 0.1, 0.1, 0.45, 0.05, 1.0, 0.0]
    hi = [0.1, 400., 5.0, 0.08, 300., 60., 60., 0.9, 0.6, 300., 100.]
    B = json.load(open(_HERE + "/fit_B2_w0.7.json")); b0 = [B[k] for k in NAMES[:-1]]
    rng = np.random.default_rng(1)
    inits = [b0 + [kl] for kl in (0.5, 2.0, 5.0, 10.0)]
    for dd in (0.025, 0.035):
        x = list(b0); x[3] = dd; inits.append(x + [3.0])
    best = None
    for x0 in inits:
        t = time.time()
        r = least_squares(res, x0, bounds=(lo, hi), diff_step=0.02, max_nfev=3000, ftol=1e-12, xtol=1e-12, gtol=1e-12, x_scale="jac")
        print(f"cost {r.cost:.5f} nfev {r.nfev} ({time.time()-t:.0f}s)", dict(zip(NAMES, np.round(r.x, 4))), flush=True)
        if best is None or r.cost < best.cost:
            best = r
    th = best.x
    json.dump(dict(zip(NAMES, map(float, th))), open(_HERE + f"/fit_BL_w{w}.json", "w"), indent=1)
    A = json.load(open(_HERE + "/fit_A.json"))
    from model_A import shape_cmd
    fg, ag, G = np.array(A["fg"]), np.array(A["ag"]), np.array(A["G"]); O = np.array(A["O"])
    rows = [E.summary(E.evaluate(D, lambda u, p0: sim_O(u, p0, *O)), "O"),
            E.summary(E.evaluate(D, lambda u, p0: sim_O(shape_cmd(u, A["tc"], A["W"], A["hk"], A["h0"], fg, ag, G), p0, *O)), "A"),
            E.summary(E.evaluate(D, lambda u, p0: sim_B(u, p0, *b0)), "B"),
            E.summary(rb := E.evaluate(D, lambda u, p0: sim_BL(u, p0, *th)), "B＋漏れ")]
    print(pd.DataFrame(rows).to_string())
    T = rb["map"]
    print(T[(T.c == 0.3) & (T.a.isin([0.02, 0.04])) & T.data.isin(["ampsweep", "sine2"])].round(2).to_string())
