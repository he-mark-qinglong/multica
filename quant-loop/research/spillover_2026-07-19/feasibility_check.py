"""Pre-SPEC feasibility check for SMA-35000."""
import os, json, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from statsmodels.tsa.api import VAR

DATA_DIR = '/home/smark/services/strategy_display_engine_data/canonical/workdir/strategies/vpvr_reversion_1m_nostop_20260630/data'
SYMS = ['BTCUSDT','ETHUSDT','SOLUSDT']

def load_log_rv_1h(sym):
    df = pd.read_parquet(os.path.join(DATA_DIR, f'fapi_{sym}__1m.parquet'))
    log_px = np.log(df['close'])
    return np.log(log_px.diff().resample('1h').apply(lambda x: np.sqrt(np.sum(x**2)))).dropna().rename(sym)

def generalized_fevd_psi(Phi, Sigma, H=10):
    Phi = np.asarray(Phi, dtype=float)
    Sigma = np.asarray(Sigma, dtype=float)
    K, p = Phi.shape[0], Phi.shape[2]
    F = np.zeros((K*p, K*p))
    F[:K, :K*p] = np.hstack([Phi[:,:,j] for j in range(p)])
    F[K:, :K*(p-1)] = np.eye(K*(p-1))
    Fh = np.eye(K*p)
    sigma_diag = np.diag(Sigma)
    theta = np.zeros((K, K))
    for h in range(1, H+1):
        Fh = Fh @ F
        A = Fh[:K,:K] @ Sigma
        A = np.asarray(A)
        for i in range(K):
            for j in range(K):
                theta[i,j] += (A[i,j]**2) / sigma_diag[j]
    return theta / theta.sum(axis=1, keepdims=True)

def spillover_metrics(psi):
    K = psi.shape[0]
    TO = psi.sum(axis=0) - np.diag(psi)
    FROM_ = psi.sum(axis=1) - np.diag(psi)
    NET = TO - FROM_
    return TO, FROM_, NET, TO.sum()/K

panel = pd.concat([load_log_rv_1h(s) for s in SYMS], axis=1).dropna()
print(f"Loaded log-RV: {panel.shape[0]} hourly bars × {panel.shape[1]} assets")
print(f"Range: {panel.index.min()} → {panel.index.max()}\n")

WINDOW=90*24; H=10; P_LIST=[1,2,4]
step = 30*24
windows = [(i, i+WINDOW) for i in range(0, panel.shape[0]-WINDOW+1, step)]
print(f"Rolling fit: W={WINDOW}h, step={step}h → {len(windows)} windows\n")

import traceback
for p in P_LIST:
    print(f"=== VAR(p={p}) ===")
    net_df = pd.DataFrame(index=range(len(windows)), columns=SYMS, dtype=float)
    tot = []; fail = 0
    for w_i,(s,e) in enumerate(windows):
        sub = panel.iloc[s:e].copy()
        sub = sub.replace([np.inf,-np.inf], np.nan).dropna()
        if sub.shape[0] < 50:
            fail += 1; continue
        try:
            res = VAR(sub).fit(p)
            Phi = np.zeros((3,3,p))
            for j in range(p):
                Phi[:,:,j] = np.asarray(res.coefs[j])
            Sigma = np.asarray(res.sigma_u)
            if not np.all(np.isfinite(Sigma)):
                fail += 1; continue
            psi = generalized_fevd_psi(Phi, Sigma, H=H)
            _,_,NET,total = spillover_metrics(psi)
            net_df.iloc[w_i] = NET; tot.append(total)
        except Exception:
            fail += 1
    tc = np.array(tot)
    print(f"  successes={len(tot)}, fails={fail}")
    if len(tot)>0:
        print(f"  total connectedness: mean={np.nanmean(tc):.3f}, std={np.nanstd(tc):.3f}, "
              f"range=[{np.nanmin(tc):.3f},{np.nanmax(tc):.3f}]")
    for w_i in range(len(windows)):
        r = net_df.iloc[w_i]
        if r.isna().all(): continue
        ranks = r.rank(ascending=False)
        rstr = " | ".join(f"{sym}:NET={r[sym]:+.3f}(r{int(ranks[sym])})" for sym in SYMS)
        print(f"    w{w_i:02d}: {rstr}")
    valid = net_df.dropna()
    if valid.shape[0] >= 2:
        from scipy.stats import spearmanr
        print("  NET stability across windows (Gate F):")
        for i_a in range(len(SYMS)):
            for i_b in range(i_a+1, len(SYMS)):
                rho,pv = spearmanr(valid[SYMS[i_a]], valid[SYMS[i_b]])
                tag='STABLE' if abs(rho)>=0.5 else ('NOISY' if abs(rho)<0.2 else 'WEAK')
                print(f"    {SYMS[i_a]} vs {SYMS[i_b]}: rho={rho:+.3f} p={pv:.3f}  [{tag}]")
        print("  Gate D (refit-noise, |std/mean|):")
        for sym in SYMS:
            m=valid[sym].mean(); sd=valid[sym].std()
            cv = abs(sd/m) if abs(m)>1e-6 else float('inf')
            tag='STABLE' if cv<0.5 else ('WEAK' if cv<1.0 else 'NOISY')
            print(f"    {sym}: mean={m:+.4f}  std={sd:.4f}  cv={cv:.2f}  [{tag}]")
    print()

with open('/tmp/spillover_check/summary.json','w') as f:
    json.dump({'panel_bars':int(panel.shape[0]), 'n_windows':len(windows), 'p_tested':P_LIST}, f, indent=2)
print("Wrote summary.json")
