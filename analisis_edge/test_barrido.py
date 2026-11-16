#!/usr/bin/env python
"""
Valida el BARRIDO DE RATIOS de analizar_log_edge.py con una simulacion de caminata aleatoria (no usa tus logs).

Se simulan trades con SL=100 ticks y TP=200 ticks (ratio 1:2) sobre un precio que sigue una caminata aleatoria, y se
escribe el log TRD con el MFE medido EXACTAMENTE como lo hace la estrategia (maximo a favor ANTES de la vela de salida;
un trade que sale en su propia vela de fill usa el rango de esa vela).
  - SIN deriva (juego justo): a 1:1 deben acertar ~50%, a 1:2 ~33%, y la expectativa bruta ~0 en todos los ratios.
  - CON deriva a favor sembrada: el barrido debe detectar expectativa positiva.
Uso:  python test_barrido.py
"""
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "analizar_log_edge.py")
SL, TP, TV, QTY, COMM = 100, 200, 0.5, 5, 5.0
STEPS_PER_BAR, MAX_BARS, SIGMA = 30, 600, 40.0 / (30 ** 0.5)   # 30 micro-pasos por vela, sigma por vela = 40 ticks


def simulate_trade(rng, mu):
    steps = rng.normal(mu / STEPS_PER_BAR, SIGMA, MAX_BARS * STEPS_PER_BAR)
    path = np.cumsum(steps)
    hit_tp = np.where(path >= TP)[0]
    hit_sl = np.where(path <= -SL)[0]
    j_tp = hit_tp[0] if len(hit_tp) else 10 ** 9
    j_sl = hit_sl[0] if len(hit_sl) else 10 ** 9
    P = path.reshape(MAX_BARS, STEPS_PER_BAR)
    opens = np.concatenate([[0.0], P[:-1, -1]])
    highs = np.maximum(opens, P.max(axis=1))
    lows = np.minimum(opens, P.min(axis=1))
    if min(j_tp, j_sl) == 10 ** 9:
        exit_name, e, out_ticks = "Exit_on_session_close", MAX_BARS - 1, float(P[-1, -1])
    elif j_tp < j_sl:
        exit_name, e, out_ticks = "Profit_target", j_tp // STEPS_PER_BAR, float(TP)
    else:
        exit_name, e, out_ticks = "Stop_loss", j_sl // STEPS_PER_BAR, float(-SL)
    if e == 0:                                   # sale en su propia vela de fill: rango de esa vela
        hi, lo, bhi, blo, bars = highs[0], lows[0], 0, 0, 1
    else:                                        # velas ANTES de la de salida
        hi, lo = highs[:e].max(), lows[:e].min()
        bhi, blo, bars = int(np.argmax(highs[:e])), int(np.argmin(lows[:e])), e
    return exit_name, out_ticks, max(hi, 0.0), max(-lo, 0.0), bhi, blo, bars


def make_log(path, mu, seed, n_days_trades=12):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-02", "2026-08-31")
    times = []
    for day in days:
        n = int(rng.integers(max(2, n_days_trades - 6), n_days_trades + 6))
        mins = np.sort(rng.integers(7 * 60 + 30, 14 * 60, n))
        times += [day + pd.Timedelta(minutes=int(m), seconds=15 * int(rng.integers(0, 4))) for m in mins]
    lines = ["CFG tf=15s | rr=2.00 | risk=250 | qty=5 | tick=0.25 | pv=2.00 | be=---- | acc=off | maxTrades=200 | velN=20 | slopeN=10 | ind=rsi10,atr10,roc20,macd20/200/9,adx10,rvol20 | models=C3b,C3s"]
    for t in times:
        ex, out_ticks, hi, lo, bhi, blo, bars = simulate_trade(rng, mu)
        gross = out_ticks * TV * QTY
        side = "BUY" if rng.random() < 0.5 else "SELL"
        f = lambda v, d=1: f"{v:.{d}f}"
        lines.append(
            f"TRD C3 {side} {t:%Y-%m-%d %H:%M:%S} | sig={'BuyC3' if side == 'BUY' else 'SellC3'} | tf=15s | qty={QTY} | pnl={gross - COMM:.2f} | comm={COMM:.2f} | fee=0.00 | "
            f"exit={ex} | mfe={hi:.1f}@{bhi} | mae={lo:.1f}@{blo} | bars={bars} | slT={SL} | tpT={TP} | p0={SL / (SL + TP):.4f} | lag=1 | vel={f(rng.normal(0, 2), 2)} | accel={f(rng.normal(0, 8), 2)} | "
            f"efic={f(rng.normal(0, 0.15), 2)} | bal={int(rng.integers(-5, 6))} | slope={f(rng.normal(0, 30))} | sep={f(rng.normal(0, 300))} | dist200={f(rng.normal(0, 300))} | "
            f"dist20={f(rng.normal(0, 20))} | dist50={f(rng.normal(0, 100))} | g39={f(rng.normal(0, 8))} | g921={f(rng.normal(0, 15))} | body={f(rng.normal(0, 20))} | "
            f"rng={f(abs(rng.normal(40, 15)))} | rsi={f(rng.normal(50, 8))} | atr={f(np.clip(rng.normal(45, 12), 10, 120))} | roc={f(rng.normal(0, 0.02), 4)} | macd={f(rng.normal(0, 30))} | "
            f"macdS={f(rng.normal(0, 30))} | macdH={f(rng.normal(0, 8))} | adx={f(np.clip(rng.normal(25, 8), 5, 70))} | rvol={f(abs(rng.normal(1, 0.3)), 2)} | accEf={f(abs(rng.normal(0.15, 0.1)), 2)} | "
            f"x1={f(abs(rng.normal(20, 15)), 2)} | x2={int(rng.integers(1, 6))}.00 | x3=0.00 | ctx=1")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return len(times)


ROW = re.compile(r"^\s+1:(\S+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([+-][\d.]+)\s+\[\s*([+-][\d.]+),\s*([+-][\d.]+)\]\s+\[\s*([+-][\d.]+),\s*([+-][\d.]+)\]")


def run(path):
    r = subprocess.run([sys.executable, TOOL, path, "--reps", "0", "--tpr", "1.0"], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print(r.stdout[-2500:], r.stderr[-2500:])
        raise SystemExit("la herramienta fallo")
    rows = {}
    for line in r.stdout.splitlines():
        m = ROW.match(line)
        if m:
            g = float(m.group(1))
            rows[g] = dict(hit=float(m.group(4)), null=float(m.group(5)), e=float(m.group(6)),
                           lo=float(m.group(7)), hi=float(m.group(8)), lo_a=float(m.group(9)), hi_a=float(m.group(10)))
    if not rows:
        print(r.stdout[-3000:])
        raise SystemExit("no se pudo leer la tabla del barrido (cambio el formato?)")
    return rows, r.stdout


def main():
    fails = 0
    with tempfile.TemporaryDirectory() as tmp:
        print("Juego JUSTO (sin deriva): 3 semillas")
        for seed in (1, 2, 3):
            p = os.path.join(tmp, f"fair{seed}.txt")
            n = make_log(p, mu=0.0, seed=seed)
            rows, out = run(p)
            bad = []
            for g, r in rows.items():
                if abs(r["e"]) >= 0.10:     # sin deriva la expectativa bruta debe quedar cerca de 0 (error tipico ~0.02-0.03R con ~2000 trades)
                    bad.append(f"E[bruta] 1:{g:g} = {r['e']:+.3f}R (lejos de 0)")
                if g in (0.5, 1.0, 2.0) and abs(r["hit"] - r["null"]) > 3.5:
                    bad.append(f"aciertos 1:{g:g} = {r['hit']:.1f}% vs azar {r['null']:.1f}%")
            ok_msg = "Ningun ratio tiene el IC ajustado por encima de 0" in out
            hit1 = rows.get(1.0, {}).get("hit", float("nan")); hit2 = rows.get(2.0, {}).get("hit", float("nan"))
            print(f"  semilla {seed}: n={n}  aciertos 1:1={hit1:.1f}% (azar 50)  1:2={hit2:.1f}% (azar 33.3)  -> {'OK' if (not bad and ok_msg) else 'FALLA ' + '; '.join(bad)}")
            fails += 0 if (not bad and ok_msg) else 1
        print("Con DERIVA a favor sembrada (3 ticks por vela): 2 semillas")
        for seed in (11, 12):
            p = os.path.join(tmp, f"drift{seed}.txt")
            n = make_log(p, mu=3.0, seed=seed)
            rows, out = run(p)
            e1, e2 = rows[1.0], rows[2.0]
            detects = e1["lo_a"] > 0 and e2["lo_a"] > 0 and "revisar" in out
            print(f"  semilla {seed}: n={n}  E[bruta] 1:1={e1['e']:+.3f}R (limite inf ajustado {e1['lo_a']:+.3f})  1:2={e2['e']:+.3f}R (limite inf ajustado {e2['lo_a']:+.3f})  -> {'OK' if detects else 'FALLA (no detecta)'}")
            fails += 0 if detects else 1
        print("Seccion 8 (features de los que 'cumplen' 1:1) presente:", "OK" if "8) QUE FEATURES CORRELACIONAN" in out else "FALLA")
        fails += 0 if "8) QUE FEATURES CORRELACIONAN" in out else 1
    print("\nTODO OK" if fails == 0 else f"\nHAY {fails} FALLAS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
