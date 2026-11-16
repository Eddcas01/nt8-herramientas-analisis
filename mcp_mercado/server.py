"""Servidor MCP 'mercado-nt8' (SIN dependencias, solo libreria estandar).

Expone el snapshot que escribe el indicador BridgeMercado_Snapshot.cs para que Claude analice el
mercado en vivo junto con el usuario. SOLO LECTURA: lee el JSON del puente, no manda ordenes.

Habla el protocolo MCP (JSON-RPC 2.0) por stdin/stdout, linea por linea. Compatible con Python 3.10+.
Lo lanza el cliente MCP via .mcp.json; no se corre a mano.

Herramientas:
  - get_snapshot()  -> estado completo (precio, EMAs, tendencia, swings, posicion, velas)
  - get_position()  -> solo posicion (lado, contratos, promedio, P&L) + precio
  - get_bars(n)     -> ultimas n velas OHLCV
"""
import datetime
import json
import os
import pathlib
import sys

SNAP = pathlib.Path(
    os.environ.get("BRIDGE_SNAPSHOT", str(pathlib.Path.home() / "Documents" / "nt8-bridge" / "snapshot.json"))
)

SERVER_INFO = {"name": "mercado-nt8", "version": "1.0.0"}
DEFAULT_PROTO = "2024-11-05"


def log(msg):
    print(f"[mercado-nt8] {msg}", file=sys.stderr, flush=True)


# ----------------------------------------------------------------- datos
def _load() -> dict:
    if not SNAP.exists():
        return {
            "error": "No hay snapshot todavia.",
            "ayuda": "Aplica el indicador 'BridgeMercado_Snapshot' en un grafico de NinjaTrader, "
                     "con el mercado abierto (conexion en vivo o Market Replay). "
                     f"Ruta esperada: {SNAP}",
        }
    try:
        data = json.loads(SNAP.read_text(encoding="utf-8"))
    except Exception as e:  # JSON a medias: el lector reintenta en la proxima llamada
        return {"error": f"snapshot ilegible (reintenta): {e}"}
    try:
        ts = datetime.datetime.fromisoformat(data["ts"])
        edad = (datetime.datetime.now() - ts).total_seconds()
        data["antiguedad_seg"] = round(edad, 1)
        if edad > 15:
            data["aviso"] = ("El snapshot tiene mas de 15 s: el indicador puede estar detenido, "
                             "NT desconectado o el mercado cerrado. Los numeros pueden no ser actuales.")
    except Exception:
        pass
    return data


def tool_get_snapshot(_args):
    return _load()


def tool_get_position(_args):
    d = _load()
    if "error" in d:
        return d
    return {
        "precio": d.get("precio"),
        "posicion": d.get("posicion"),
        "ts": d.get("ts"),
        "antiguedad_seg": d.get("antiguedad_seg"),
        "aviso": d.get("aviso"),
    }


def tool_get_bars(args):
    d = _load()
    if "error" in d:
        return d
    n = int(args.get("n", 25) or 25)
    velas = d.get("velas", [])
    if n > 0:
        velas = velas[-n:]
    return {
        "instrumento": d.get("instrumento"),
        "timeframe": d.get("timeframe"),
        "ts": d.get("ts"),
        "velas": velas,
    }


TOOLS = [
    {
        "name": "get_snapshot",
        "description": (
            "Estado actual del mercado del grafico activo en NinjaTrader: instrumento, timeframe, "
            "precio, EMA rapida y lenta, tendencia, swing high/low recientes, tu posicion (lado, "
            "contratos, promedio, P&L abierto) y las ultimas velas (OHLCV). Trae 'antiguedad_seg' "
            "para saber que tan fresco es el dato."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "_fn": tool_get_snapshot,
    },
    {
        "name": "get_position",
        "description": (
            "Solo la posicion abierta: lado (LONG/SHORT/FLAT), contratos, promedio y P&L abierto, "
            "mas el precio actual. Para hablar de gestion (donde promediar, donde esta la canasta) "
            "con el dato real."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "_fn": tool_get_position,
    },
    {
        "name": "get_bars",
        "description": "Ultimas n velas (OHLCV) del grafico activo, de mas vieja a mas nueva.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Cuantas velas devolver (default 25).", "default": 25}
            },
            "additionalProperties": False,
        },
        "_fn": tool_get_bars,
    },
]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


# ----------------------------------------------------------------- protocolo
def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def reply(req_id, result):
    send({"jsonrpc": "2.0", "id": req_id, "result": result})


def reply_error(req_id, code, message):
    send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def handle(msg):
    method = msg.get("method")
    req_id = msg.get("id")
    is_request = req_id is not None

    if method == "initialize":
        proto = (msg.get("params") or {}).get("protocolVersion", DEFAULT_PROTO)
        reply(req_id, {
            "protocolVersion": proto,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
        return

    if method in ("notifications/initialized", "initialized"):
        return  # notificacion, sin respuesta

    if method == "ping":
        if is_request:
            reply(req_id, {})
        return

    if method == "tools/list":
        listed = [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
                  for t in TOOLS]
        reply(req_id, {"tools": listed})
        return

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOLS_BY_NAME.get(name)
        if not tool:
            reply_error(req_id, -32602, f"herramienta desconocida: {name}")
            return
        try:
            result = tool["_fn"](args)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            reply(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:
            reply(req_id, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
        return

    if is_request:
        reply_error(req_id, -32601, f"metodo no soportado: {method}")


def main():
    log(f"iniciado. snapshot = {SNAP}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as e:
            log(f"json invalido: {e}")
            continue
        try:
            handle(msg)
        except Exception as e:
            log(f"error manejando mensaje: {e}")


if __name__ == "__main__":
    main()
