"""Painel de configuração do USRP: uma página e rotas JSON, só biblioteca padrão.

    GET  /                 a página
    GET  /api/state        estado, configuração, o que o aparelho aceitou
    GET  /api/spectrum     espectro do último bloco recebido, em dB
    POST /api/config       salva a configuração (e reconecta se estiver recebendo)
    POST /api/connect      conectar (e continuar tentando)
    POST /api/disconnect   desconectar
    POST /api/probe        procura um USRP num IP, sem abrir sessão

Sem autenticação: ferramenta de bancada. O compose publica a porta só em
127.0.0.1.
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from grs_iq_rx_usrp.config import SdrConfig
from grs_iq_rx_usrp.service import SdrService

MAX_BODY_BYTES = 16 * 1024


def make_handler(service: SdrService) -> type[BaseHTTPRequestHandler]:
    page = resources.files("grs_iq_rx_usrp").joinpath("panel.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass  # o painel consulta o estado várias vezes por segundo

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: HTTPStatus, payload) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("Content-Length inválido") from None
            if length > MAX_BODY_BYTES:
                raise ValueError("corpo grande demais")
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length))

        def do_GET(self) -> None:
            if self.path == "/":
                self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json(HTTPStatus.OK, service.state())
            elif self.path == "/api/spectrum":
                self._json(HTTPStatus.OK, service.spectrum_db())
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "rota desconhecida"})

        def do_POST(self) -> None:
            try:
                body = self._body()
                if self.path == "/api/config":
                    service.update_config(SdrConfig.from_dict(body))
                elif self.path == "/api/connect":
                    service.connect()
                elif self.path == "/api/disconnect":
                    service.disconnect()
                elif self.path == "/api/probe":
                    address = body.get("address") if isinstance(body, dict) else None
                    if address is not None and not isinstance(address, str):
                        raise ValueError("address: esperava texto")
                    try:
                        found = service.probe(address)
                    except RuntimeError as error:
                        self._json(HTTPStatus.OK, {"found": [], "error": str(error)})
                        return
                    self._json(HTTPStatus.OK, {"found": found})
                    return
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "rota desconhecida"})
                    return
            except (ValueError, UnicodeDecodeError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return

            self._json(HTTPStatus.OK, service.state())

    return Handler


def start_panel(service: SdrService, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(service))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="panel").start()

    return server
