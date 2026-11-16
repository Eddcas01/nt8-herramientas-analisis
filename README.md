# Herramientas para NinjaTrader 8: análisis estadístico, compilación y conector MCP

Utilidades que construí para desarrollar estrategias de trading algorítmico en **NinjaTrader 8 (C# /
NinjaScript)** con rigor estadístico. La idea central es **distinguir un patrón real (edge) del azar** antes de
arriesgar dinero.

| Carpeta | Qué hace | Tecnologías |
|---|---|---|
| [`analisis_edge/`](analisis_edge) | Análisis **patrón contra azar** del log de operaciones | Python · pandas · numpy |
| [`nt8check/`](nt8check) | **Verificador de compilación** de NinjaScript fuera de NinjaTrader | C# · Roslyn · PowerShell |
| [`mcp_mercado/`](mcp_mercado) + [`indicador/`](indicador) | **Conector MCP** que le da a Claude datos de mercado en vivo desde NT8 | Python (stdlib) · MCP · C# |

---

## `analisis_edge/`: ¿patrón o azar?

`analizar_log_edge.py` lee el log por operación (`TRD`) que emite la estrategia y emite un **veredicto
automático**, calibrado para dar alrededor de un 10 % de falsas alarmas cuando no hay edge.

- **Validación del log**: emparejamiento orden-contexto y desfase constante entre la vela de señal y la de fill.
- **Edge global**: expectativa bruta por operación con **IC agrupado por días** y carrera TP/SL contra
  `p0 = SL/(SL+TP)`, que es la tasa de aciertos que daría el azar.
- **Homogeneidad** por lado, hora, día y mes.
- **Criba de features con FDR** (Benjamini-Hochberg), con una fase de **descubrimiento** y otra de
  **confirmación fuera de muestra**.
- **Walk-forward** mes a mes con la selección hecha dentro de cada paso y un **nulo por desplazamiento
  circular**.
- **Barrido de ratios** (0.5R a 2R) a partir del MFE/MAE de cada operación, con IC ajustado por comparaciones
  múltiples.

Las pruebas de la herramienta usan **datos sintéticos**:
- `test_herramienta.py`: 6 logs de juego justo (debe responder "sin edge") y 3 con una señal sembrada (debe
  detectarla).
- `test_barrido.py`: caminatas aleatorias en las que sin deriva debe salir alrededor de 50 % a 1:1 y de
  33 % a 1:2.

```bash
python analisis_edge/analizar_log_edge.py "log.txt" --csv trades.csv --modelo C3 --corte 2026-06-01
python analisis_edge/test_herramienta.py     # ~2 min
```

`analizar_volumen_trd.py` agrupa las operaciones por volumen relativo en quintiles con IC95.
`patrones_1m.py` prueba hipótesis de sesión **pre-registradas** sobre velas de 1 minuto; la ruta de los datos
se configura con la variable de entorno `NT8_BARRAS`.

## `nt8check/`: compilar NinjaScript sin abrir NinjaTrader

Compila uno o varios `.cs` con **el mismo Roslyn y las mismas referencias que usa NT8**, sin emitir nada, y
lista los errores. Sirve para detectar fallos antes de copiar el archivo a NinjaTrader y como paso de CI local.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File nt8check\nt8check.ps1 MiEstrategia.cs [-w]
```

Requiere NinjaTrader 8 instalado. El ejecutable se compila una sola vez con el `csc` de .NET Framework.

## `mcp_mercado/`: Claude conectado al mercado en vivo

```
NinjaTrader (indicador)  →  snapshot.json  →  server.py (MCP stdio)  →  Claude
```

- **`indicador/BridgeMercado_Snapshot.cs`**: indicador NT8 que escribe cada N segundos el precio, las EMAs,
  la tendencia, los swings, la posición abierta y las últimas velas. Usa **escritura atómica** y formato
  invariante. **No envía órdenes.**
- **`mcp_mercado/server.py`**: servidor **Model Context Protocol** implementado a mano, solo con la
  biblioteca estándar. Expone `get_snapshot`, `get_position` y `get_bars` con la antigüedad del dato.

Detalles de instalación en [`mcp_mercado/README.md`](mcp_mercado/README.md).

---

> Uso educativo y de investigación. Nada de esto es una recomendación de inversión.
