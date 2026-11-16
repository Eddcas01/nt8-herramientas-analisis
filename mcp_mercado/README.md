# Conector MCP `mercado-nt8`

Puente de solo lectura entre **NinjaTrader 8** y **Claude**, para analizar el mercado en vivo juntos
sin exportar ni pegar nada.

```
NinjaTrader (indicador)  ->  Documents/nt8-bridge/snapshot.json  ->  server.py (MCP)  ->  Claude
```

## Piezas

1. **`indicador/BridgeMercado_Snapshot.cs`** — indicador NT8. Escribe `snapshot.json` cada N seg
   con: precio, EMA rapida/lenta, tendencia, swing high/low, tu posicion (lado, contratos, promedio,
   P&L) y las ultimas velas. **No manda ordenes.**
2. **`server.py`** (esta carpeta) — servidor MCP. Lee el JSON y lo expone como herramientas.
3. **`.mcp.json`** — registra el conector en Claude Code (ver ejemplo abajo).

## Instalacion (una vez)

1. En NinjaTrader: copia/compila `BridgeMercado_Snapshot.cs` (F5) y agregalo a un grafico del
   instrumento/timeframe que quieras analizar. Revisa el parametro **Cuenta** y **Ruta del archivo JSON**.
2. Python: no necesita nada extra. `server.py` usa solo la libreria estandar (Python 3.10+).
3. En Claude Code, al abrir el proyecto, aprueba el servidor MCP `mercado-nt8` cuando lo pida
   (lo define `.mcp.json`). Si no aparece, reinicia la sesion.

## Uso

Con NT conectado en vivo (o Market Replay) y el indicador en el grafico, Claude puede llamar:

- `get_snapshot()` — foto completa del mercado + tu posicion.
- `get_position()` — solo tu posicion y el precio.
- `get_bars(n)` — ultimas n velas OHLCV.

Cada respuesta trae `antiguedad_seg`: si es alta, el dato esta viejo (NT detenido o mercado cerrado).

## Notas

- La **ruta** del JSON debe ser identica en el indicador y en `server.py` (o define la variable de
  entorno `BRIDGE_SNAPSHOT`).
- Escritura **atomica** (`.tmp` -> copia): si el lector cae justo en una escritura, reintenta solo.
- Todo en **formato invariante** (punto decimal) para que el JSON sea valido aunque Windows este en es-GT.
- El JSON es data efimera; no hace falta versionarlo.

### Ejemplo de `.mcp.json`

```json
{
  "mcpServers": {
    "mercado-nt8": { "command": "python", "args": ["mcp_mercado/server.py"] }
  }
}
```
