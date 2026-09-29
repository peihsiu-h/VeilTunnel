"""FastAPI application: decoy website + WebSocket tunnel endpoint."""

from __future__ import annotations

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

from server.banlist import BanList
from server.tunnel import handle_websocket

WS_PATH = "/ws/updates"

# Decoy static page (makes the server look like an ordinary website)
DECOY_HTML = """\
<!DOCTYPE html>
<html><head><title>Welcome</title></head>
<body><h1>It works!</h1><p>This is the default web page for this server.</p></body>
</html>
"""


def create_app(psk: str, banlist: BanList | None = None, auto_ban: bool = True) -> FastAPI:
    # No /docs, /redoc or /openapi.json: they would give the game away.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    async def index():
        return HTMLResponse(DECOY_HTML)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.websocket(WS_PATH)
    async def websocket_tunnel(ws: WebSocket):
        await handle_websocket(ws, psk, banlist, auto_ban)

    return app
