# Analiza el volumen de la vela de entrada en un log TRD de la familia LogEdge (campos vol / volAvg / rvol).
# Responde: en que rango de volumen estaban los trades que salieron bien?
#
# Uso:  python herramientas\analisis_edge\analizar_volumen_trd.py "<NinjaScript Output ....txt>" [n_grupos=5]
#
# Parte los trades en grupos de igual tamano (quintiles por defecto) segun vol y segun rvol, y para cada grupo da:
#   n, rango, % de aciertos, p0 medio (aciertos esperados SIN ventaja), neto $/trade con IC95 y el signo del neto
#   en cada mitad del periodo. Un rango solo es interesante si el neto es positivo, el IC95 excluye 0 y las dos
#   mitades tienen el mismo signo. Con 2 variables x 5 grupos se miran 10 cortes: uno "bueno" por azar es esperable,
#   asi que un rango encontrado aqui es una HIPOTESIS a confirmar en datos nuevos, no un filtro listo.
import re, sys, math, statistics as st

def leer(path):
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            txt = open(path, encoding=enc).read()
            if "TRD " in txt:
                break
        except UnicodeError:
            continue
    filas = []
    for ln in txt.splitlines():
        if not ln.startswith("TRD "):
            continue
        partes = [p.strip() for p in ln.split("|")]
        cab = partes[0].split()
        d = {"lado": cab[2], "t": cab[3] + " " + cab[4]}
        for p in partes[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                d[k] = v
        if "vol" not in d:
            continue
        try:
            filas.append({"t": d["t"], "lado": d["lado"], "pnl": float(d["pnl"]), "p0": float(d.get("p0", "nan")),
                          "vol": float(d["vol"]), "volAvg": float(d["volAvg"]), "rvol": float(d["rvol"])})
        except ValueError:
            pass
    filas.sort(key=lambda r: r["t"])
    return filas

def resumen(g):
    n = len(g)
    pn = [r["pnl"] for r in g]
    m = st.mean(pn)
    se = st.stdev(pn) / math.sqrt(n) if n > 1 else float("nan")
    win = 100 * sum(1 for x in pn if x > 0) / n
    p0 = 100 * st.mean(r["p0"] for r in g)
    return n, win, p0, m, m - 1.96 * se, m + 1.96 * se, sum(pn)

def tabla(filas, campo, k, corte_t):
    orden = sorted(filas, key=lambda r: r[campo])
    n = len(orden)
    print(f"\n== Por {campo} ({k} grupos de igual tamano) ==")
    print(f"{'rango':>21} {'n':>5} {'gana%':>6} {'p0%':>6} {'$/trade':>9} {'IC95':>19} {'neto$':>9}  mitades")
    for i in range(k):
        g = orden[i * n // k:(i + 1) * n // k]
        if not g:
            continue
        nn, win, p0, m, lo, hi, tot = resumen(g)
        a = [r["pnl"] for r in g if r["t"] < corte_t]
        b = [r["pnl"] for r in g if r["t"] >= corte_t]
        ma = st.mean(a) if a else float("nan")
        mb = st.mean(b) if b else float("nan")
        marca = "  <-- candidato" if lo > 0 and ma > 0 and mb > 0 else ""
        print(f"{g[0][campo]:>9.2f} a {g[-1][campo]:>9.2f} {nn:>5} {win:>6.1f} {p0:>6.1f} {m:>9.2f} [{lo:>7.2f},{hi:>8.2f}] {tot:>9.0f}  {ma:>+7.2f} / {mb:>+7.2f}{marca}")

def main():
    if len(sys.argv) < 2:
        print(__doc__ or "uso: analizar_volumen_trd.py <log> [n_grupos]")
        sys.exit(1)
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    filas = leer(sys.argv[1])
    if not filas:
        print("No hay lineas TRD con vol=. Recompila la estrategia LogEdge (version con volumen) y vuelve a correr el backtest.")
        sys.exit(1)
    nn, win, p0, m, lo, hi, tot = resumen(filas)
    print(f"Trades con volumen: {nn} | {filas[0]['t']} a {filas[-1]['t']}")
    print(f"Total: gana {win:.1f}% (p0 {p0:.1f}%) | ${m:.2f}/trade IC95 [{lo:.2f}, {hi:.2f}] | neto ${tot:.0f}")
    gan = [r for r in filas if r["pnl"] > 0]
    per = [r for r in filas if r["pnl"] <= 0]
    for nombre, g in (("Ganadores", gan), ("Perdedores", per)):
        if g:
            v = sorted(r["vol"] for r in g); rv = sorted(r["rvol"] for r in g)
            q = lambda x, p: x[min(len(x) - 1, int(p * len(x)))]
            print(f"{nombre:>10}: n={len(g)} | vol mediana {q(v, .5):.0f} (25%-75%: {q(v, .25):.0f}-{q(v, .75):.0f}) | rvol mediana {q(rv, .5):.2f} (25%-75%: {q(rv, .25):.2f}-{q(rv, .75):.2f})")
    corte_t = filas[len(filas) // 2]["t"]
    print(f"Mitades: antes / desde {corte_t}")
    for campo in ("vol", "rvol"):
        tabla(filas, campo, k, corte_t)
    print("\n'candidato' = neto positivo con IC95 sobre 0 y positivo en las dos mitades. Es una hipotesis: confirmarla en datos nuevos.")

if __name__ == "__main__":
    main()
