"""O painel por HTTP de verdade: servidor real numa porta real."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

from fakes import fake_uhd_module
from grs_iq_rx_usrp.config import SdrConfig, load_config
from grs_iq_rx_usrp.panel import start_panel
from grs_iq_rx_usrp.service import SdrService


@pytest.fixture
def panel(tmp_path):
    uhd = fake_uhd_module(found=[{"type": "usrp2", "addr": "192.168.10.2", "product": "N210r4"}])
    config_path = tmp_path / "sdr.json"
    service = SdrService(SdrConfig(publish_bind="tcp://127.0.0.1:*"), uhd,
                         config_path=config_path, retry_interval_s=0.2)
    service.start()
    server = start_panel(service, "127.0.0.1", 0)
    yield service, f"http://127.0.0.1:{server.server_address[1]}", config_path
    server.shutdown()
    server.server_close()
    service.stop()


def http(method, url, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"null") \
                if response.headers.get("Content-Type") == "application/json" else response.read()
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_pagina_e_servida(panel):
    _, base, _ = panel
    status, body = http("GET", base + "/")

    assert status == 200
    assert b"GRS IQ RX" in body and b"Testar conex" in body


def test_estado_traz_a_configuracao(panel):
    _, base, _ = panel
    status, state = http("GET", base + "/api/state")

    assert status == 200
    assert state["config"]["address"] == "192.168.10.2"
    assert state["state"] == "desconectado"


def test_salvar_grava_no_disco_e_valida(panel):
    service, base, config_path = panel
    new = {**service.config.to_dict(), "address": "10.1.2.3", "gain_db": 30}

    status, state = http("POST", base + "/api/config", new)

    assert status == 200
    assert state["config"]["address"] == "10.1.2.3"
    assert load_config(config_path).address == "10.1.2.3"


def test_configuracao_invalida_devolve_400_sem_gravar(panel):
    service, base, config_path = panel

    status, body = http("POST", base + "/api/config",
                        {**service.config.to_dict(), "clock_source": "atomico"})

    assert status == 400
    assert "clock_source" in body["error"]
    assert not config_path.exists()


def test_probe_pelo_painel(panel):
    _, base, _ = panel
    status, body = http("POST", base + "/api/probe", {"address": "192.168.10.2"})

    assert status == 200
    assert body["found"][0]["product"] == "N210r4"


def test_conectar_e_desconectar_pelo_painel(panel):
    service, base, _ = panel

    http("POST", base + "/api/connect")
    deadline = time.monotonic() + 5
    while service.state()["state"] != "recebendo" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert service.state()["state"] == "recebendo"

    status, state = http("POST", base + "/api/disconnect")
    assert status == 200 and state["want_running"] is False


def test_rota_desconhecida_404(panel):
    _, base, _ = panel
    assert http("GET", base + "/nada")[0] == 404
    assert http("POST", base + "/api/nada", {})[0] == 404
