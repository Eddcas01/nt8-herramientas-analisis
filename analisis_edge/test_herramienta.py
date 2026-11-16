#!/usr/bin/env python
"""
Prueba de la herramienta de analisis con datos SINTETICOS (no usa tus logs):
  - caso NULO: resultados al azar (12.1% de SL), features al azar  -> debe decir "SIN EDGE NI PATRON DETECTABLE".
  - caso con SENAL SEMBRADA: la prob. de SL baja cuando 'efic' y 'accEf' son altas -> debe detectarla.
Uso:  python test_herramienta.py
"""
import math
import os
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "analizar_log_edge.py")


def make_log(path, signal, seed):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-02", "2026-08-31")
    rows = []
    for day in days:
        n = rng.integers(8, 28)
        mins = np.sort(rng.integers(7 * 60 + 30, 14 * 60, n))
        for m in mins:
            rows.append((day + pd.Timedelta(minutes=int(m), seconds=int(rng.integers(0, 4)) * 15)))
    n = len(rows)
    side = rng.choice(["BUY", "SELL"], n)
    efic = rng.normal(0, 0.15, n)
    accef = np.clip(np.abs(rng.normal(0.15, 0.1, n)), 0, 1)
    adx = np.clip(rng.normal(25, 8, n), 5, 70)
    atr = np.clip(rng.normal(45, 12, n), 10, 120)
    p_fair = 60.0 / 460.0                      # juego justo: SL con prob. tpT/(slT+tpT) => media BRUTA = 0
    logit = np.full(n, math.log(p_fair / (1 - p_fair)))
    if signal:
        ze, za = (efic - efic.mean()) / efic.std(), (accef - accef.mean()) / accef.std()
        logit = logit - 0.55 * ze - 0.45 * za
    sl = rng.random(n) < 1 / (1 + np.exp(-logit))
    lines = ["CFG tf=15s | rr=0.15 | risk=1000 | qty=5 | tick=0.25 | pv=2.00 | be=---- | acc=off | maxTrades=200 | velN=10 | slopeN=8 | ind=rsi14,atr14,roc10,macd12/26/9,adx14,rvol20 | models=C3b,C3s"]
    for i in range(n):
        t = rows[i]
        f = lambda v, d=1: f"{v:.{d}f}"
        lines.append(
            f"TRD C3 {side[i]} {t:%Y-%m-%d %H:%M:%S} | sig={'BuyC3' if side[i] == 'BUY' else 'SellC3'} | tf=15s | qty=5 | "
            f"pnl={-1005.0 if sl[i] else 145.0:.2f} | comm=5.00 | fee=0.00 | exit={'Stop_loss' if sl[i] else 'Profit_target'} | "
            f"mfe=30.0@1 | mae=20.0@1 | bars=5 | slT=400 | tpT=60 | p0=0.8696 | lag=1 | vel={f(rng.normal(0, 2), 2)} | accel={f(rng.normal(0, 8), 2)} | "
            f"efic={f(efic[i], 2)} | bal={int(rng.integers(-5, 6))} | slope={f(rng.normal(0, 30))} | sep={f(rng.normal(0, 300))} | dist200={f(rng.normal(0, 300))} | "
            f"dist20={f(rng.normal(0, 20))} | dist50={f(rng.normal(0, 100))} | g39={f(rng.normal(0, 8))} | g921={f(rng.normal(0, 15))} | body={f(rng.normal(0, 20))} | "
            f"rng={f(abs(rng.normal(40, 15)))} | rsi={f(rng.normal(50, 8))} | atr={f(atr[i])} | roc={f(rng.normal(0, 0.02), 4)} | macd={f(rng.normal(0, 30))} | "
            f"macdS={f(rng.normal(0, 30))} | macdH={f(rng.normal(0, 8))} | adx={f(adx[i])} | rvol={f(abs(rng.normal(1, 0.3)), 2)} | accEf={f(accef[i], 2)} | "
            f"x1={f(abs(rng.normal(20, 15)), 2)} | x2={int(rng.integers(1, 6))}.00 | x3=0.00 | ctx=1")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return n


def run(path, extra=()):
    r = subprocess.run([sys.executable, TOOL, path, "--reps", "5", *extra], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit("la herramienta fallo")
    return r.stdout


def main():
    fails = 0
    null_alarms = 0
    with tempfile.TemporaryDirectory() as tmp:
        print("Caso NULO (juego justo, sin senal): 6 semillas distintas")
        for seed in (1, 2, 3, 4, 5, 6):
            p = os.path.join(tmp, f"null{seed}.txt")
            n = make_log(p, signal=False, seed=seed)
            out = run(p)
            ok = "SIN EDGE NI PATRON DETECTABLE" in out
            print(f"  semilla {seed}: n={n}  veredicto sin edge -> {'OK' if ok else 'alarma (esperable ~10% de las veces)'}")
            null_alarms += 0 if ok else 1
        print("Caso con SENAL SEMBRADA (efic y accEf altas -> menos SL): 3 semillas distintas")
        for seed in (11, 12, 13):
            p = os.path.join(tmp, f"sig{seed}.txt")
            n = make_log(p, signal=True, seed=seed)
            out = run(p)
            found = "HAY SENALES A VALIDAR" in out
            disc_line = [l for l in out.splitlines() if l.startswith("features con q_IS<0.10")]
            has_feat = bool(disc_line) and ("efic" in disc_line[0] or "accEf" in disc_line[0])
            print(f"  semilla {seed}: n={n}  detecta senal -> {'OK' if found else 'FALLA (no detecta)'}   | {disc_line[0][:110] if disc_line else ''}")
            fails += 0 if (found and has_feat) else 1
    # Con 3 criterios al 95% la tasa de alarmas sin edge real es ~10%: con 6 semillas, 3 o mas alarmas (p~1.6%) indican un problema.
    print(f"\nalarmas en el caso nulo: {null_alarms} de 6 (esperable ~0.6; se acepta hasta 2)")
    if null_alarms > 2:
        fails += 1
    print("TODO OK" if fails == 0 else f"HAY {fails} FALLAS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
