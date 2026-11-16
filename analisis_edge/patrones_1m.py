# Patrones de sesion en NQ 1 minuto: mide las 4 hipotesis pre-registradas en strat/PLAN.md
# ("Patrones de sesion en NQ 1 minuto", 2026-11-15). No cambiar definiciones despues de ver resultados.
# Uso:  python herramientas\analisis_edge\patrones_1m.py MNQ 20200203 20250430   -> otro instrumento / tramo
#       python herramientas\analisis_edge\patrones_1m.py            -> solo tramo de busqueda
#       python herramientas\analisis_edge\patrones_1m.py apartado H3  -> ademas mide H3 en el tramo apartado (UNA vez)
import os, sys, glob, math
import numpy as np, pandas as pd
from statistics import NormalDist
ND = NormalDist()   # n ~ 280 dias: la normal aproxima bien a la t

DIR = os.environ.get("NT8_BARRAS", os.path.join(os.path.expanduser("~"), "Documents", "NinjaTrader 8", "export", "barras", "%s_DEC26_1Minute_*.csv"))
INSTR = "NQ"
COSTO = 1.0
TICK = 0.25
BUSQ = ("20250501", "20260630")
APAR = ("20260701", "20261104")

def cargar():
    d = pd.concat([pd.read_csv(f, sep=';', dtype={'fecha': str, 'hora_cierre_ET': str}) for f in sorted(glob.glob(DIR % INSTR))])
    d = d[(d.hora_cierre_ET > '093000') & (d.hora_cierre_ET <= '160000')].sort_values(['fecha', 'hora_cierre_ET'])
    dias = {}
    for f, g in d.groupby('fecha'):
        if len(g) >= 385:
            dias[f] = g.set_index('hora_cierre_ET')
    return dias

def P(g, hhmm):
    k = hhmm + '00'
    if k in g.index:
        return float(g.at[k, 'close'])
    prev = g[g.index <= k]
    return float(prev.close.iloc[-1])

def operaciones(dias):
    fechas = sorted(dias)
    out = {h: [] for h in ('H1', 'H2', 'H3', 'H4')}
    for i, f in enumerate(fechas):
        g = dias[f]
        ap = float(g.open.iloc[0])
        # H1 ruptura del rango de apertura
        r = g[g.index <= '100000']
        hi, lo = r.high.max(), r.low.min()
        w = g[(g.index > '100000') & (g.index <= '153000')]
        rest = g[g.index > '100000']
        for k, b in w.iterrows():
            up, dn = b.high > hi, b.low < lo
            if up and dn:
                break
            if up or dn:
                d = 1 if up else -1
                ent = hi + TICK if up else lo - TICK
                stop = lo if up else hi
                despues = rest[(rest.index > k) & (rest.index <= '155500')]
                toca = despues[(despues.low <= stop)] if up else despues[(despues.high >= stop)]
                sal = stop if len(toca) else P(g, '1555')
                out['H1'].append((f, d * (sal - ent) - COSTO))
                break
        if i > 0:
            ayer = float(dias[fechas[i - 1]].close.iloc[-1])
            # H2 impulso intradia
            s = np.sign(P(g, '1000') - ayer)
            if s != 0:
                out['H2'].append((f, s * (P(g, '1600') - P(g, '1530')) - COSTO))
            # H4 noche -> dia
            s = np.sign(ap - ayer)
            if s != 0:
                out['H4'].append((f, s * (P(g, '1555') - ap) - COSTO))
        # H3 manana -> tarde
        s = np.sign(P(g, '1200') - ap)
        if s != 0:
            out['H3'].append((f, s * (P(g, '1555') - P(g, '1200')) - COSTO))
    return out

def linea(nombre, x, conf):
    x = np.array(x, float); n = len(x)
    if n < 3:
        return f"{nombre}: n={n}"
    m = x.mean(); se = x.std(ddof=1) / math.sqrt(n)
    t = ND.inv_cdf(0.5 + conf / 2)
    p1 = 1 - ND.cdf(m / se)
    return (f"{nombre}: n={n} | gana {100 * (x > 0).mean():.1f}% | media {m:+.2f} pt (${2 * m:+.2f}/MNQ) | "
            f"IC{100 * conf:.2f}% [{m - t * se:+.2f}, {m + t * se:+.2f}] | p unilateral {p1:.3f} | total {x.sum():+.0f} pt")

def main():
    global INSTR, BUSQ
    # argumentos opcionales: <instrumento> <desde> <hasta> (tramo de busqueda), ej. MNQ 20200203 20250430
    if len(sys.argv) >= 4 and sys.argv[1] != 'apartado':
        INSTR, BUSQ = sys.argv[1], (sys.argv[2], sys.argv[3])
        del sys.argv[1:4]
    dias = cargar()
    ops = operaciones(dias)
    print(f"{INSTR} | dias completos: {len(dias)} | costo {COSTO} pt por operacion")
    print(f"\n== BUSQUEDA {BUSQ[0]} a {BUSQ[1]} ==")
    for h, v in ops.items():
        b = [(f, x) for f, x in v if BUSQ[0] <= f <= BUSQ[1]]
        x = [p for _, p in b]; mit = len(x) // 2
        m1, m2 = np.mean(x[:mit]), np.mean(x[mit:])
        m = np.mean(x); se = np.std(x, ddof=1) / math.sqrt(len(x)); t = ND.inv_cdf(0.5 + 0.9875 / 2)
        pasa = m > 0 and m - t * se > 0 and m1 > 0 and m2 > 0
        print(linea(h, x, 0.9875)); print(f"     mitades {m1:+.2f} / {m2:+.2f} pt | {'PASA' if pasa else 'no pasa'}")
    if len(sys.argv) > 2 and sys.argv[1] == 'apartado':
        print(f"\n== APARTADO {APAR[0]} a {APAR[1]} (se mira UNA vez) ==")
        for h in sys.argv[2:]:
            x = [p for f, p in ops[h] if APAR[0] <= f <= APAR[1]]
            print(linea(h, x, 0.95))

if __name__ == "__main__":
    main()
