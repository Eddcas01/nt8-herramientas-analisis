#!/usr/bin/env python
"""
Analisis "patron o azar" del log TRD de las versiones LogEdge (5s / 15s / 2m / 5m).

Uso:
    python analizar_log_edge.py "log indicators.txt" [--csv "trades.csv|xlsx"] [--modelo C3]
                                [--corte 2026-06-01 | --frac 0.6] [--reps 100] [--seed 1]

Que hace (protocolo anti-overfitting; solo necesita pandas y numpy):
  0. Integridad: lineas TRD (ctx/lag) y, si das el export del Analyzer, cruce de pnl/qty/salida.
  1. Edge global: media BRUTA por trade (debe ser ~0 si no hay edge) con IC95 por DIAS, y carrera TP-vs-SL contra
     la probabilidad nula p0 = SL/(SL+TP).
  2. Homogeneidad por lado / hora / dia de semana / mes (permutacion): cambia la tasa de perdida segun la categoria?
  3. Criba de features (AUC perdedor-vs-ganador) con descubrimiento en la 1a parte y confirmacion en la 2a,
     corrigiendo por comparaciones multiples (Benjamini-Hochberg).
  4. Walk-forward mes a mes de un puntaje compuesto (seleccion de features DENTRO de cada paso) + IC por dias +
     nulo por desplazamiento circular + efecto economico de saltarse las entradas de mayor riesgo.
  5. Persistencia de regimen: agrupacion de perdidas por dia, memoria entre trades, "candado" tras el 1er SL.
  6. Potencia: cuantos trades hacen falta para confirmar una mejora de winrate.
  Termina con un VEREDICTO automatico.

Nota: una criba SIN confirmacion fuera de muestra no demuestra nada; con decenas de features, algunas "funcionan"
por azar. Valida siempre en un periodo que NO uso para descubrir el patron.
"""
import argparse
import math
import re
import sys

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------------------------------------------
# Estadistica basica (numpy)
# ----------------------------------------------------------------------------------------------------------------


def norm_sf(z):
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def two_sided_p(z):
    return math.erfc(abs(z) / math.sqrt(2.0))


def rankdata(a):
    a = np.asarray(a, dtype=float)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    sa = a[order]
    i, n = 0, len(a)
    while i < n:
        j = i
        while j + 1 < n and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def auc_mw(x, y):
    """AUC (y=1 positivo), z y p de dos colas (Mann-Whitney con correccion por empates)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y).astype(bool)
    ok = ~np.isnan(x)
    x, y = x[ok], y[ok]
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan"), float("nan"), float("nan")
    r = rankdata(x)
    u = r[y].sum() - n1 * (n1 + 1) / 2.0
    auc = u / (n1 * n0)
    n = n1 + n0
    _, counts = np.unique(x, return_counts=True)
    tie = (counts ** 3 - counts).sum()
    var = n1 * n0 / 12.0 * ((n + 1) - tie / (n * (n - 1)))
    if var <= 0:
        return auc, float("nan"), float("nan")
    z = (u - n1 * n0 / 2.0) / math.sqrt(var)
    return auc, z, two_sided_p(z)


def bh_fdr(pvals):
    p = np.asarray(pvals, dtype=float)
    q = np.full(len(p), np.nan)
    ok = np.where(~np.isnan(p))[0]
    if len(ok) == 0:
        return q
    po = p[ok]
    order = np.argsort(po)
    m = len(po)
    ranked = po[order] * m / (np.arange(m) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(ranked, 1.0)
    q[ok] = out
    return q


def cluster_boot_mean(values, clusters, n_boot=4000, seed=1, ci=95.0):
    """Media con IC (95% por defecto) y p (vs 0) por bootstrap de CLUSTERS (dias)."""
    v = np.asarray(values, dtype=float)
    uniq, inv = np.unique(np.asarray(clusters), return_inverse=True)
    sums = np.bincount(inv, weights=v)
    cnts = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    k = len(uniq)
    means = np.empty(n_boot)
    for b in range(n_boot):
        idx = rng.integers(0, k, k)
        means[b] = sums[idx].sum() / cnts[idx].sum()
    mean = v.mean()
    lo, hi = np.percentile(means, [(100.0 - ci) / 2.0, 100.0 - (100.0 - ci) / 2.0])
    p = float((np.abs(means - means.mean()) >= abs(mean)).mean())
    return mean, lo, hi, max(p, 1.0 / n_boot)


def cluster_boot_auc(pred, y, days, n_boot=1500, seed=3):
    uniq, inv = np.unique(np.asarray(days), return_inverse=True)
    idx_by_day = [np.where(inv == i)[0] for i in range(len(uniq))]
    rng = np.random.default_rng(seed)
    a = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        ii = np.concatenate([idx_by_day[p] for p in pick])
        if y[ii].sum() == 0 or y[ii].sum() == len(ii):
            continue
        a.append(auc_mw(pred[ii], y[ii])[0])
    return tuple(np.percentile(a, [2.5, 97.5]))


def profit_factor(pnl):
    pnl = np.asarray(pnl, dtype=float)
    g, l = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    return g / l if l > 0 else float("inf")


def winsor_std(X_fit, X_apply, lo=1, hi=99):
    L = np.nanpercentile(X_fit, lo, axis=0)
    H = np.nanpercentile(X_fit, hi, axis=0)
    Xw = np.clip(X_fit, L, H)
    mu, sd = Xw.mean(axis=0), Xw.std(axis=0)
    sd[sd == 0] = 1.0
    return (Xw - mu) / sd, (np.clip(X_apply, L, H) - mu) / sd


# ----------------------------------------------------------------------------------------------------------------
# Carga
# ----------------------------------------------------------------------------------------------------------------

RAWDIR = ["slope", "slope3", "slope9", "sep", "dist200", "dist20", "dist50", "g39", "g921", "body", "macd", "macdS", "macdH", "roc"]
ATRN = ["sep_f", "dist200_f", "dist20_f", "dist50_f", "body_f", "rng", "macdH_f", "slope_f"]
TF_SECONDS = {"5s": 5, "15s": 15, "2m": 120, "5m": 300}


def load_trd(path):
    rows, cfg = [], None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("CFG "):
                cfg = line
                continue
            if not line.startswith("TRD "):
                continue
            head, *rest = line.split(" | ")
            p = head.split(" ")
            d = {"model": p[1], "side": p[2], "sigtime": pd.Timestamp(p[3] + " " + p[4])}
            for kv in rest:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    d[k] = v
            rows.append(d)
    if not rows:
        sys.exit("No se encontraron lineas TRD en " + path)
    df = pd.DataFrame(rows)
    for col in ("mfe", "mae"):
        if col in df:
            sp = df[col].str.split("@", expand=True)
            df[col] = pd.to_numeric(sp[0], errors="coerce")
            df[col + "_bar"] = pd.to_numeric(sp[1], errors="coerce")
    for c in df.columns:
        if c in ("model", "side", "sigtime", "sig", "exit", "tf"):
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df, cfg


def money(s):
    s = s.astype(str).str.strip()
    neg = s.str.startswith("-") | s.str.startswith("(")
    out = pd.to_numeric(s.str.replace(r"[-$(),\s]", "", regex=True), errors="coerce")
    return out.where(~neg, -out)


def load_analyzer(path):
    if path.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path, index_col=False)
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    et = df["Entry time"]
    if not pd.api.types.is_datetime64_any_dtype(et):
        try:
            et = pd.to_datetime(et, format="%d/%m/%Y %H:%M:%S")
        except Exception:
            et = pd.to_datetime(et, dayfirst=True)
    df["entry_time"] = et
    for c in ("Profit", "Commission"):
        if c in df and not pd.api.types.is_numeric_dtype(df[c]):
            df[c] = money(df[c])
    return df


def prepare(t):
    d = t.copy()
    d["dir"] = np.where(d["side"] == "BUY", 1.0, -1.0)
    for c in RAWDIR:
        if c in d:
            d[c + "_f"] = d[c] * d["dir"]
    if "rsi" in d:
        d["rsi_f"] = (d["rsi"] - 50.0) * d["dir"]
    d["day"] = d.sigtime.dt.normalize()
    d["month"] = d.sigtime.dt.to_period("M")
    d["hour"] = d.sigtime.dt.hour
    d["dow"] = d.sigtime.dt.dayofweek
    d["mod"] = d.sigtime.dt.hour * 60 + d.sigtime.dt.minute
    d["gross"] = d.pnl + d.get("comm", 0) + d.get("fee", 0)
    ex = d["exit"].astype(str)
    d["tp"] = (ex == "Profit_target").astype(int)
    d["sl"] = (ex == "Stop_loss").astype(int)
    for c in ATRN:
        if c in d and "atr" in d:
            d[c + "_atr"] = d[c] / d["atr"].replace(0, np.nan)
    return d


def feature_family(d):
    base = ["vel", "accel", "efic", "bal", "slope_f", "slope3_f", "slope9_f", "sep_f", "dist200_f", "dist20_f", "dist50_f", "g39_f", "g921_f", "body_f",
            "macd_f", "macdS_f", "macdH_f", "roc_f", "rsi_f", "rng", "atr", "adx", "rvol", "accEf", "x1", "x2", "x3"]
    fam = [f for f in base if f in d and d[f].notna().any() and d[f].nunique() > 1]
    fam += [c + "_atr" for c in ATRN if (c + "_atr") in d and d[c + "_atr"].nunique() > 1]
    fam += ["mod", "dow"]
    return fam


# ----------------------------------------------------------------------------------------------------------------
# Secciones del analisis
# ----------------------------------------------------------------------------------------------------------------

def H(txt):
    print("\n" + "=" * 118 + "\n" + txt + "\n" + "=" * 118)


def sec_integridad(t, cfg, csv_path):
    H("0) INTEGRIDAD DEL LOG")
    print("CFG:", cfg)
    print(f"lineas TRD={len(t)}  rango {t.sigtime.min()} -> {t.sigtime.max()}  dias={t.sigtime.dt.date.nunique()}")
    for col in ("ctx", "lag", "model", "tf", "qty", "exit"):
        if col in t:
            print(f"  {col}: {dict(t[col].value_counts().sort_index())}")
    if "ctx" in t and (t.ctx != 1).any():
        print("  AVISO: hay trades con ctx != 1 (contexto emparejado por direccion o ausente): revisar antes de confiar en las features.")
    if csv_path:
        a = load_analyzer(csv_path)
        tf = str(t["tf"].iloc[0]) if "tf" in t else "15s"
        off = pd.Timedelta(seconds=TF_SECONDS.get(tf, 15))
        x = t.copy()
        x["entry_time"] = x.sigtime + off
        x["pos"] = np.where(x.side == "BUY", "Long", "Short")
        m = x.merge(a, left_on=["entry_time", "pos"], right_on=["entry_time", "Market pos."], how="outer", indicator=True)
        b = m[m["_merge"] == "both"]
        print(f"  cruce con el Analyzer: emparejados={len(b)} de {len(t)} TRD y {len(a)} filas del Analyzer "
              f"(solo TRD={int((m._merge == 'left_only').sum())}, solo Analyzer={int((m._merge == 'right_only').sum())})")
        print(f"  pnl igual: {int(np.isclose(b.pnl, b.Profit).sum())}/{len(b)}   qty igual: {int((b.qty == b.Qty).sum())}/{len(b)}")


def sec_edge(d):
    H("1) EDGE GLOBAL: comparacion con el azar")
    N = len(d)
    print(f"N={N}  neto=${d.pnl.sum():,.0f} (media ${d.pnl.mean():.2f}/trade, PF {profit_factor(d.pnl):.3f})   "
          f"bruto=${d.gross.sum():,.0f} (media ${d.gross.mean():.2f}/trade, PF {profit_factor(d.gross):.3f})   comisiones+tarifas=${(d.gross - d.pnl).sum():,.0f}")
    print("salidas:", {k: f"{v} ({v / N * 100:.1f}%)" for k, v in d["exit"].value_counts().items()})
    for k, g in d.groupby("exit"):
        print(f"   {k:24s} n={len(g):5d}  pnl medio=${g.pnl.mean():8.2f}  total=${g.pnl.sum():,.0f}")
    m, lo, hi, p = cluster_boot_mean(d.gross.values, d.day.values)
    print(f"\nMEDIA BRUTA por trade = ${m:.2f}  IC95 por dias [{lo:.2f}, {hi:.2f}]  p vs 0 = {p:.3f}")
    print("   (sin edge la media bruta es ~0 por teorema del paro opcional: la neta = -costos)")
    ex = d["exit"].astype(str)
    race = d[(ex == "Profit_target") | (ex == "Stop_loss")]     # por NOMBRE de salida (no depende del modo de objetivo)
    if len(race) > 0 and "p0" in race and race.p0.gt(0).all():
        k, n = int((race["exit"] == "Profit_target").sum()), len(race)
        exp = race.p0.sum()
        z = (k - exp) / math.sqrt((race.p0 * (1 - race.p0)).sum())
        print(f"Carrera TP-vs-SL ({n} trades resueltos): TP={k} ({k / n * 100:.2f}%)  azar esperado={exp / n * 100:.2f}%  z={z:+.2f}  p={two_sided_p(z):.3f}")
        print("   (OJO: los trades que no resolvieron por TP/SL -cierre de sesion, etc.- quedan fuera y eso sesga un poco el TP% hacia arriba; la media bruta de arriba si los incluye)")


def chi2_codes(codes, k, y):
    """Estadistico chi2 de homogeneidad (k categorias x 2 resultados) con numpy puro (rapido para permutaciones)."""
    tab = np.bincount(codes * 2 + y.astype(int), minlength=2 * k).reshape(k, 2).astype(float)
    exp = tab.sum(1, keepdims=True) * tab.sum(0, keepdims=True) / tab.sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        v = (tab - exp) ** 2 / exp
    return float(np.nansum(v))


def sec_homog(d, target, rng):
    H("2) HOMOGENEIDAD: tasa de perdida segun lado / hora / dia / mes / modelo (chi2 por permutacion)")
    for col in ("side", "hour", "dow", "month", "model"):
        if col not in d or d[col].nunique() < 2:
            continue
        codes, uniq = pd.factorize(d[col].astype(str))
        k = len(uniq)
        obs = chi2_codes(codes, k, target)
        y = target.copy()
        cnt = 0
        for _ in range(3000):
            rng.shuffle(y)
            if chi2_codes(codes, k, y) >= obs:
                cnt += 1
        print(f"  {col:6s}: {k:2d} categorias  chi2={obs:7.2f}  p(perm)={(cnt + 1) / 3001:.3f}")


def split_masks(d, corte, frac):
    if corte:
        c = pd.Timestamp(corte)
    else:
        order = np.sort(d.sigtime.values)
        c = pd.Timestamp(order[int(len(order) * frac)])
    return (d.sigtime < c).values, c


def sec_features(d, fam, race_mask, is_mask, corte_ts):
    H(f"3) CRIBA DE FEATURES (perdedor vs ganador). Descubrir en la 1a parte (< {corte_ts.date()}), confirmar en la 2a")
    r = d[race_mask]
    isr = is_mask[race_mask]
    IS, OOS = r[isr], r[~isr]
    print(f"trades de la carrera: {len(r)}  IS={len(IS)} (perdedores {int(IS.sl.sum())})  OOS={len(OOS)} (perdedores {int(OOS.sl.sum())})  | features probadas: {len(fam)}")
    rows = []
    for f in fam:
        a_all, _, p_all = auc_mw(r[f], r.sl)
        a_is, z_is, p_is = auc_mw(IS[f], IS.sl)
        a_os, z_os, p_os = auc_mw(OOS[f], OOS.sl)
        rows.append(dict(feature=f, auc_IS=a_is, p_IS=p_is, auc_OOS=a_os, p_OOS=p_os, z_OOS=z_os, auc_all=a_all, p_all=p_all))
    R = pd.DataFrame(rows)
    R["q_IS"] = bh_fdr(R.p_IS.values)
    R["q_all"] = bh_fdr(R.p_all.values)
    R["p_OOS_1c"] = [norm_sf(row.z_OOS * np.sign(row.auc_IS - 0.5)) for row in R.itertuples()]
    R = R.sort_values("p_IS")
    sh = R[["feature", "auc_IS", "p_IS", "q_IS", "auc_OOS", "p_OOS_1c", "auc_all", "q_all"]].head(12).copy()
    for c in ("auc_IS", "auc_OOS", "auc_all"):
        sh[c] = sh[c].round(3)
    for c in ("p_IS", "q_IS", "p_OOS_1c", "q_all"):
        sh[c] = sh[c].map(lambda v: f"{v:.4f}")
    print(sh.to_string(index=False))
    print("(AUC>0.5: feature ALTA => mas perdedores; AUC<0.5: feature BAJA => mas perdedores. q = p ajustado por # de features)")
    disc = R[R.q_IS < 0.10]
    conf = disc[(disc.p_OOS_1c < 0.05)]
    print(f"\nfeatures con q_IS<0.10 (descubiertas): {list(disc.feature)}")
    print(f"... de ellas confirmadas en OOS (p 1 cola < 0.05, mismo signo): {list(conf.feature)}")
    sdr = math.sqrt((len(r) + 1) / (12.0 * max(1, r.sl.sum()) * max(1, len(r) - r.sl.sum())))
    print(f"error tipico del AUC con toda la muestra ~{sdr:.3f}: solo se detectan efectos de |AUC-0.5| >= ~{2.8 * sdr:.3f}")
    return R, disc, conf


def composite_wf(d, fam, race_mask, p_sel, min_train=2):
    months = sorted(d.month.unique())
    score = pd.Series(np.nan, index=d.index)
    nsel = []
    for mi, m in enumerate(months):
        if mi < min_train:
            continue
        tr = d[(d.month < m) & race_mask]
        te = d[d.month == m]
        if len(tr) < 100 or tr.sl.sum() < 10:
            continue
        Xtr_s, Xte_s = winsor_std(tr[fam].values, te[fam].values)
        sel, sgn = [], []
        for j, f in enumerate(fam):
            a, z, p = auc_mw(tr[f], tr.sl)
            if p < p_sel:
                sel.append(j)
                sgn.append(1.0 if a > 0.5 else -1.0)
        nsel.append(len(sel))
        score.loc[te.index] = (Xte_s[:, sel] * np.array(sgn)).sum(axis=1) / math.sqrt(len(sel)) if sel else 0.0
    return score, nsel


def sec_walkforward(d, fam, race_mask, reps, rng):
    H("4) WALK-FORWARD: puntaje compuesto (pesos iguales, seleccion de features DENTRO de cada paso, entrena con meses previos)")
    months = sorted(d.month.unique())
    if len(months) < 4:
        print("  menos de 4 meses de datos: se omite el walk-forward.")
        return None
    results = {}
    for p_sel in (0.05, 0.20, 0.35):
        score, nsel = composite_wf(d, fam, race_mask, p_sel)
        tm = score.notna().values & race_mask
        if tm.sum() < 200:
            continue
        y = d.sl.values[tm].astype(float)
        a, z, p = auc_mw(score.values[tm], y)
        lo, hi = cluster_boot_auc(score.values[tm], y, d.day.values[tm])
        results[p_sel] = (a, lo, hi)
        print(f"  p_sel<{p_sel:4.2f}: AUC walk-forward = {a:.3f}  IC95 por dias [{lo:.3f}, {hi:.3f}]  (n={int(tm.sum())}, perdedores={int(y.sum())}, p iid={p:.3f}, features/mes mediana={int(np.median(nsel)) if nsel else 0})")
    if 0.20 not in results:
        return None
    score, _ = composite_wf(d, fam, race_mask, 0.20)
    tm_all = score.notna().values
    pn = d.pnl.values[tm_all]
    print(f"\n  Efecto economico (p_sel<0.20), todos los trades de los meses de prueba (n={tm_all.sum()}, media ${pn.mean():.2f}/trade): saltarse el X% de mayor riesgo")
    sv = score.values[tm_all]
    for q in (10, 20, 30):
        skip = sv >= np.percentile(sv, 100 - q)
        eff = np.where(skip, -pn, 0.0)
        m_, lo, hi, _ = cluster_boot_mean(eff, d.day.values[tm_all])
        print(f"   top {q}%: saltados={int(skip.sum()):4d}  perdedores% saltados={d.sl.values[tm_all][skip].mean() * 100:5.1f} vs resto={d.sl.values[tm_all][~skip].mean() * 100:5.1f}  "
              f"efecto=${-pn[skip].sum():8,.0f} (al azar ~${-pn.mean() * skip.sum():,.0f})  por trade=${m_:.2f} [IC95 {lo:.2f}, {hi:.2f}]")
    # nulo: desplazamiento circular de las etiquetas de la carrera
    if reps > 0:
        idx = np.where(race_mask)[0]
        lab = d.sl.values[idx].copy()
        orig_sl, orig_tp = d.sl.values.copy(), d.tp.values.copy()
        aucs = []
        for _ in range(reps):
            shift = rng.integers(len(lab) // 10, len(lab) - len(lab) // 10)
            ls = np.roll(lab, shift)
            d.loc[d.index[idx], "sl"] = ls
            d.loc[d.index[idx], "tp"] = 1 - ls
            sc, _ = composite_wf(d, fam, race_mask, 0.20)
            tm = sc.notna().values & race_mask
            aucs.append(auc_mw(sc.values[tm], d.sl.values[tm].astype(float))[0])
        d["sl"], d["tp"] = orig_sl, orig_tp
        aucs = np.array(aucs)
        a_real = results[0.20][0]
        print(f"\n  Nulo del procedimiento ({reps} desplazamientos circulares): AUC nulo media={aucs.mean():.3f} sd={aucs.std():.3f} p95={np.percentile(aucs, 95):.3f}  |  AUC real={a_real:.3f}  p(nulo>=real)={(aucs >= a_real).mean():.3f}")
        results["null_p"] = float((aucs >= a_real).mean())
    return results


def tick_value(cfg):
    """Valor de 1 tick por contrato (tick * pv) leido de la linea CFG; 0.50 (MNQ) si no esta."""
    m1 = re.search(r"tick=([\d.]+)", cfg or "")
    m2 = re.search(r"pv=([\d.]+)", cfg or "")
    return float(m1.group(1)) * float(m2.group(1)) if (m1 and m2) else 0.5


def sweep_outcomes(d, r_mult, tv):
    """
    Resultado de cada trade si el TP hubiera sido r_mult x SL (con el MISMO SL; solo valido para r_mult <= TP/SL de la corrida).
    Un trade 'alcanzo' el TP' si salio por TP, o si su MFE (maxima excursion a favor ANTES de salir) >= TP': el TP' se
    habria ejecutado antes que el SL. Conservador: un SL en la MISMA vela del fill (bars<=1) no cuenta como acierto, porque
    dentro de una vela no se sabe si el maximo vino antes que el minimo.
    Devuelve (alcanzo, resultado_bruto_en_ticks, sl_ticks).
    """
    sl_t = d.slT.values.astype(float)
    tp_t = r_mult * sl_t
    ex = d["exit"].astype(str).values
    is_tp, is_sl = ex == "Profit_target", ex == "Stop_loss"
    reached = is_tp | (d.mfe.values >= tp_t)
    reached = np.where(is_sl & (d.bars.values <= 1), False, reached)
    gross_ticks = (d.pnl.values + d.comm.values + d.fee.values) / (d.qty.values * tv)
    out = np.where(reached, tp_t, np.where(is_sl, -sl_t, gross_ticks))
    return reached, out, sl_t


def sec_barrido(d, cfg):
    H("7) BARRIDO DE RATIOS: con el SL de la corrida, que habria pasado con TP a 0.5R, 1R, 1.5R, 2R... (usa el MFE de cada trade)")
    if not {"slT", "tpT", "mfe", "bars"}.issubset(d.columns) or not (d.slT > 0).all():
        print("  el log no trae slT/tpT/mfe/bars: se omite.")
        return None
    kmax = float((d.tpT / d.slT).max())
    tv = tick_value(cfg)
    # Ratios < 0.5 NO se evaluan: el MFE se mide por vela y no incluye lo que subio el precio dentro de la vela de salida, asi que
    # los aciertos de un TP' muy cercano a la entrada se subcuentan (sesgo a la baja). El ratio de la propia corrida es exacto.
    grid = [g for g in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0) if g <= kmax + 1e-9]
    if not any(abs(g - kmax) < 1e-9 for g in grid):
        grid.append(round(kmax, 4))
    grid = sorted(set(grid))
    sl_ticks = float(d.slT.median())
    print(f"  corrida con SL={sl_ticks:.0f} ticks y TP={float(d.tpT.median()):.0f} ticks (ratio 1:{kmax:g}); valor del tick por contrato=${tv:.2f}; n={len(d)} trades")
    print("  Solo se pueden evaluar TP' <= TP de la corrida (con el SL fijo). 'alcanzo' = salio por TP o su MFE llego a TP' antes de salir.")
    print("  Se evaluan ratios >= 0.5 (mas cerca de la entrada el MFE por vela subcuenta aciertos). El ratio de la propia corrida es exacto.")
    print("  Los fills del backtest son al precio exacto del TP/SL (sin slippage): la expectativa real seria algo peor, sobre todo con muchos SL.")
    ci_adj = 100.0 - 5.0 / len(grid)           # IC con Bonferroni por mirar varios ratios
    print(f"  IC95 por dias de la expectativa BRUTA en R (R = SL) y, entre parentesis, el IC ajustado por mirar {len(grid)} ratios ({ci_adj:.1f}%)")
    print("   ratio  TP(ticks)  alcanzaron  aciertos%  azar%   E[bruta] R   IC95 por dias        IC ajustado        E[neta] R   $neto/trade")
    best = None
    rows = []
    for g in grid:
        reached, out, sl_t = sweep_outcomes(d, g, tv)
        er = out / sl_t
        cost_r = (d.comm.values + d.fee.values) / (d.qty.values * tv) / sl_t
        m, lo, hi, p = cluster_boot_mean(er, d.day.values)
        _, lo_a, hi_a, _ = cluster_boot_mean(er, d.day.values, ci=ci_adj)
        net = float((er - cost_r).mean())
        usd = net * float((sl_t * tv * d.qty.values).mean())
        n_rea = int(reached.sum())
        print(f"   1:{g:<5g} {g * sl_ticks:8.0f}   {n_rea:6d}     {reached.mean() * 100:6.2f}   {100 / (1 + g):5.1f}   {m:+8.3f}   [{lo:+7.3f}, {hi:+7.3f}]   [{lo_a:+7.3f}, {hi_a:+7.3f}]   {net:+8.3f}   {usd:+9.1f}")
        rows.append((g, m, lo_a, hi_a, net))
        if best is None or lo_a > best[2]:
            best = (g, m, lo_a, hi_a, net)
    print("   (azar% = probabilidad de acierto SIN edge = 1/(1+ratio); sin edge la E[bruta] es ~0 en todos los ratios)")
    g, m, lo_a, hi_a, net = best
    if lo_a > 0:
        print(f"  Mejor ratio por limite inferior del IC ajustado: 1:{g:g} con E[bruta] {m:+.3f}R, limite inferior {lo_a:+.3f}R > 0 (revisar; confirmar en un periodo nuevo).")
    else:
        print(f"  Ningun ratio tiene el IC ajustado por encima de 0 (el mejor, 1:{g:g}, tiene E[bruta] {m:+.3f}R con limite inferior {lo_a:+.3f}R): sin edge demostrado. "
              "Elegir el 'mejor' ratio mirando solo la media es seleccionar ruido.")
    # MAE / MFE: calor de los que alcanzan 1R y excursion de los que no
    g1 = 1.0 if kmax >= 1.0 else grid[len(grid) // 2]
    reached, _, sl_t = sweep_outcomes(d, g1, tv)
    q = [10, 25, 50, 75, 90]
    mae_r = d.mae.values / sl_t
    mfe_r = d.mfe.values / sl_t
    print(f"\n  MAE/MFE (en R = SL).  Los que SI alcanzaron 1:{g1:g} ({int(reached.sum())}): MAE antes de ganar, percentiles {q}: {np.round(np.percentile(mae_r[reached], q), 2) if reached.any() else '-'}")
    print(f"                        Los que NO lo alcanzaron ({int((~reached).sum())}): MFE maximo, percentiles {q}: {np.round(np.percentile(mfe_r[~reached], q), 2) if (~reached).any() else '-'}")
    sh = d.groupby("side").apply(lambda x: pd.Series({"n": len(x), f"alcanzan 1:{g1:g} %": sweep_outcomes(x, g1, tv)[0].mean() * 100}), include_groups=False)
    print("  por lado:", {k: (int(v["n"]), round(float(v[f"alcanzan 1:{g1:g} %"]), 1)) for k, v in sh.iterrows()})
    return rows


def retarget(d, r_mult, tv):
    """Copia de d donde 'acierto' = alcanzar r_mult x SL antes del SL: sirve para buscar que features correlacionan con los que 'cumplen'."""
    reached, _, _ = sweep_outcomes(d, r_mult, tv)
    d2 = d.copy()
    is_sl = (d2["exit"].astype(str) == "Stop_loss").values
    d2["tp"] = reached.astype(int)
    d2["sl"] = (~reached & is_sl).astype(int)
    mask = reached | is_sl
    return d2, mask


def sec_regimen(d):
    H("5) PERSISTENCIA DE REGIMEN (agrupacion de perdidas por dia, memoria entre trades)")
    d = d.sort_values("sigtime").reset_index(drop=True)
    p = d.sl.mean()
    g = d.groupby("day").agg(n=("sl", "size"), sl=("sl", "sum"))
    exp_var = (g.n * p * (1 - p)).sum()
    obs_var = ((g.sl - g.n * p) ** 2).sum()
    rng = np.random.default_rng(5)
    sims = [((rng.binomial(g.n.values, p) - g.n.values * p) ** 2).sum() / exp_var for _ in range(1500)]
    print(f"  dispersion de SL por dia: observada/esperada = {obs_var / exp_var:.3f} (1 = independientes; >1 = se agrupan)  p={(np.array(sims) >= obs_var / exp_var).mean():.3f}")
    d["prev_sl"] = d.sl.shift(1)
    same = d.day == d.day.shift(1)
    sub = d[((d.tp == 1) | (d.sl == 1)) & same & d.prev_sl.notna()]
    a, b = sub[sub.prev_sl == 1].sl, sub[sub.prev_sl == 0].sl
    if len(a) > 20 and len(b) > 20:
        se = math.sqrt(a.mean() * (1 - a.mean()) / len(a) + b.mean() * (1 - b.mean()) / len(b))
        z = (a.mean() - b.mean()) / se if se > 0 else 0.0
        print(f"  P(SL | anterior fue SL) = {a.mean() * 100:.2f}% (n={len(a)})  vs  P(SL | anterior no fue SL) = {b.mean() * 100:.2f}% (n={len(b)})  dif={(a.mean() - b.mean()) * 100:+.2f}pp  p={two_sided_p(z):.3f}")
    d["sl_before"] = d.groupby("day").sl.cumsum() - d.sl
    after = d[d.sl_before >= 1]
    if len(after):
        m_, lo, hi, _ = cluster_boot_mean(np.where(d.sl_before >= 1, -d.pnl, 0.0), d.day.values)
        print(f"  Candado 'no operar tras el 1er SL del dia': dejaria de tomar {len(after)} trades ({len(after) / len(d) * 100:.1f}%); efecto=${-after.pnl.sum():,.0f} "
              f"(al azar ~${-d.pnl.mean() * len(after):,.0f}); por trade=${m_:.2f} [IC95 {lo:.2f}, {hi:.2f}]")


def sec_potencia(d):
    H("6) POTENCIA: trades necesarios para CONFIRMAR una mejora de winrate (80% potencia, 5% dos colas)")
    ex = d["exit"].astype(str)
    is_tp, is_sl = ex == "Profit_target", ex == "Stop_loss"
    race = d[is_tp | is_sl]
    pb = (race["exit"] == "Profit_target").mean() if len(race) else 0.87
    win = d.pnl[is_tp].mean() if is_tp.any() else 100.0
    loss = -d.pnl[is_sl].mean() if is_sl.any() else 500.0
    print(f"  base: winrate de la carrera {pb * 100:.2f}%   ganancia media ${win:.1f}   perdida media ${loss:.1f}   -> cada +1pp de winrate vale ${(win + loss) * 0.01:.1f}/trade")
    for delta in (0.5, 1.0, 2.0, 3.0, 4.0):
        n = (1.96 + 0.8416) ** 2 * pb * (1 - pb) * 2 / (delta / 100) ** 2
        print(f"   +{delta:.1f}pp (= +${(win + loss) * delta / 100:5.1f}/trade): {n:8,.0f} trades por grupo")


def veredicto(d, R, disc, conf, wf):
    H("VEREDICTO AUTOMATICO")
    m, lo, hi, p = cluster_boot_mean(d.gross.values, d.day.values)
    edge_bruto = lo > 0 or hi < 0
    disparos = []
    print(f"- Media bruta por trade ${m:.2f} IC95 [{lo:.2f}, {hi:.2f}]: " + ("DISTINTA de 0 (revisar)" if edge_bruto else "indistinguible de 0 (sin edge global)"))
    if edge_bruto:
        disparos.append("edge bruto global")
    print(f"- Features descubiertas (q_IS<0.10): {len(disc)};  confirmadas fuera de muestra: {len(conf)}")
    if len(conf):
        disparos.append("features confirmadas en OOS: " + ", ".join(conf.feature))
    if wf:
        a, lo2, hi2 = wf.get(0.20, (np.nan, np.nan, np.nan))
        wf_ok = lo2 > 0.5
        print(f"- Walk-forward compuesto AUC {a:.3f} IC95 [{lo2:.3f}, {hi2:.3f}]: " + ("por ENCIMA de 0.5 (revisar)" if wf_ok else "incluye 0.5 (sin habilidad demostrada)"))
        if wf_ok:
            disparos.append("walk-forward con AUC > 0.5")
    if not disparos:
        print("\n=> SIN EDGE NI PATRON DETECTABLE con estos datos. Los indicios sueltos (si los hay) estan dentro del ruido; no operar sobre ellos.")
    else:
        print("\n=> HAY SENALES A VALIDAR (" + "; ".join(disparos) + ").")
        print("   No son prueba: con 3 criterios al 95% la tasa de falsas alarmas sin edge real es ~10%. Confirmalas en un periodo NUEVO (no usado")
        print("   para descubrirlas) antes de convertirlas en filtro o en regla de operacion.")


# ----------------------------------------------------------------------------------------------------------------

def analizar(t, cfg, args):
    d = prepare(t)
    rng = np.random.default_rng(args.seed)
    fam = feature_family(d)
    ex_race = d[(d.tp == 1) | (d.sl == 1)]
    # modo carrera (C3-like) si casi todas las salidas son TP/SL; si no (reversiones) el objetivo es "perder" (pnl<0)
    if len(ex_race) / len(d) >= 0.9:
        race_mask = ((d.tp == 1) | (d.sl == 1)).values
        mode = "carrera TP/SL (perdedor = SL)"
    else:
        d["sl"] = (d.pnl < 0).astype(int)
        d["tp"] = (d.pnl >= 0).astype(int)
        race_mask = np.ones(len(d), bool)
        mode = "todos los trades (perdedor = pnl<0), por salidas de reversion/otras"
    print(f"\n>>> modelo={d.model.iloc[0]}  tf={d['tf'].iloc[0] if 'tf' in d else '?'}  modo de objetivo: {mode}")
    sec_edge(d)
    sec_homog(d[race_mask].reset_index(drop=True), d.sl.values[race_mask].copy(), rng)
    is_mask, c = split_masks(d, args.corte, args.frac)
    R, disc, conf = sec_features(d, fam, race_mask, is_mask, c)
    wf = sec_walkforward(d, fam, race_mask, args.reps, rng)
    sec_regimen(d)
    sec_potencia(d)
    sec_barrido(d, cfg)
    if args.tpr:
        # Objetivo alternativo: 'cumple' = alcanzo args.tpr x SL antes del SL (p.ej. 1.0 = ratio 1:1) -> que features correlacionan con los que cumplen
        tv = tick_value(cfg)
        if "mfe" in d and (d.slT > 0).all() and args.tpr <= float((d.tpT / d.slT).max()) + 1e-9:
            d2, mask2 = retarget(d, args.tpr, tv)
            H(f"8) QUE FEATURES CORRELACIONAN CON LOS TRADES QUE 'CUMPLEN' 1:{args.tpr:g} (acierto = alcanzar {args.tpr:g}R antes del SL; perdedor = SL antes)")
            print(f"  cumplen: {int(d2.tp.sum())} de {len(d2)} trades ({d2.tp.mean() * 100:.1f}%);  perdedores por SL: {int(d2.sl.sum())};  azar sin edge = {100 / (1 + args.tpr):.1f}%")
            sec_features(d2, fam, mask2, is_mask, c)
            sec_walkforward(d2, fam, mask2, 0, rng)
        else:
            print(f"\n(--tpr {args.tpr} supera el ratio de la corrida o falta el MFE: se omite)")
    veredicto(d, R, disc, conf, wf)


def main():
    ap = argparse.ArgumentParser(description="Analisis patron-vs-azar del log TRD de LogEdge")
    ap.add_argument("log", help="archivo de texto con las lineas TRD/CFG (copiado de la ventana Output de NT)")
    ap.add_argument("--csv", help="export de trades del Analyzer (csv/xlsx) para cruzar y validar el log", default=None)
    ap.add_argument("--modelo", help="analizar solo este modelo (p.ej. C3, C10)", default=None)
    ap.add_argument("--corte", help="fecha de corte IS/OOS (YYYY-MM-DD); por defecto el 60%% de los trades", default=None)
    ap.add_argument("--frac", type=float, default=0.6, help="fraccion cronologica para el descubrimiento (si no hay --corte)")
    ap.add_argument("--reps", type=int, default=100, help="repeticiones del nulo por desplazamiento circular (0 = omitir)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tpr", type=float, default=None,
                    help="ademas, buscar que features correlacionan con los trades que 'cumplen' este multiplo del SL (p.ej. 1.0 = 1:1, 2.0 = 1:2); debe ser <= ratio de la corrida")
    args = ap.parse_args()

    t, cfg = load_trd(args.log)
    sec_integridad(t, cfg, args.csv)
    models = [args.modelo] if args.modelo else sorted(t.model.unique())
    for mdl in models:
        sub = t[t.model == mdl].sort_values("sigtime").reset_index(drop=True)
        if len(sub) < 150:
            print(f"\n(modelo {mdl}: solo {len(sub)} trades; se necesitan >=150 para analizar)")
            continue
        analizar(sub, cfg, args)


if __name__ == "__main__":
    main()
