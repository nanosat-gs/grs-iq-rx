"""Ponto de entrada: receptor USRP (python3-uhd) + painel de configuração.

    PUB  :5556   IQ, um lote por mensagem, sem tópico, cf32_le — o mesmo
                 contrato do grs-iq-rx em C, então demodulador e gravador
                 não sabem qual dos dois está do outro lado
    SUB  :5557   `tune` do frequency-synthesizer (opcional)
    HTTP :8091   painel: endereço do USRP, recepção, teste de conexão

Configuração em GRS_IQ_RX_CONFIG (JSON num volume), editada pelo painel.
"""

from __future__ import annotations

import logging
import os
import pathlib
import signal
import sys
import threading

from grs_iq_rx_usrp.config import load_config
from grs_iq_rx_usrp.device import import_uhd
from grs_iq_rx_usrp.panel import start_panel
from grs_iq_rx_usrp.service import SdrService

DEFAULT_CONFIG_PATH = "/app/config/sdr.json"
DEFAULT_PANEL_PORT = 8091


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("grs_iq_rx_usrp")

    config_path = pathlib.Path(os.environ.get("GRS_IQ_RX_CONFIG", DEFAULT_CONFIG_PATH))
    try:
        config = load_config(config_path)
    except ValueError as error:
        # Arquivo inválido derruba o boot com a mensagem inteira, em vez de
        # subir com o padrão e receber na frequência errada sem ninguém saber.
        log.error("configuração inválida em %s: %s", config_path, error)
        return 1

    uhd = import_uhd()
    service = SdrService(config, uhd, config_path=config_path)
    service.start()

    port = int(os.environ.get("GRS_IQ_RX_PANEL_PORT", DEFAULT_PANEL_PORT))
    panel = start_panel(service, "0.0.0.0", port)
    log.info("painel em http://0.0.0.0:%d/ — configuração em %s", port, config_path)
    if not config.autoconnect:
        log.info("conexão automática desligada: conecte pelo painel")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()

    panel.shutdown()
    service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
