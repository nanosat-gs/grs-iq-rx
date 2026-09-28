"""O serviço de ponta a ponta, com o UHD falso e ZMQ de verdade.

O IQ é conferido do lado de fora, por um SUB real na porta publicada: o
contrato é o do grs-iq-rx em C (um lote por mensagem, sem tópico, cf32_le),
e um teste que lesse o estado interno não pegaria um envelope errado.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
import zmq

from fakes import FakeMultiUSRP, fake_uhd_module, tone
from grs_iq_rx_usrp.config import SdrConfig
from grs_iq_rx_usrp.service import SdrService


def free_port() -> int:
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    port = socket.bind_to_random_port("tcp://127.0.0.1")
    socket.close(linger=0)
    context.term()
    return port


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def running():
    """Serviço de pé com UHD falso; devolve (service, uhd, porta_iq)."""
    services = []

    def start(**overrides):
        uhd = fake_uhd_module(found=[{"type": "usrp2", "addr": "192.168.10.2",
                                      "product": "N210r4", "serial": "F1234"}])
        port = free_port()
        config = SdrConfig(publish_bind=f"tcp://127.0.0.1:{port}", **overrides)
        service = SdrService(config, uhd, retry_interval_s=0.2)
        service.start()
        services.append(service)
        return service, uhd, port

    yield start
    for service in services:
        service.stop()


def last_device() -> FakeMultiUSRP:
    assert FakeMultiUSRP.instances, "nenhuma sessão foi aberta"
    return FakeMultiUSRP.instances[-1]


def test_sem_autoconnect_fica_desconectado_esperando_o_painel(running):
    service, _, _ = running()
    time.sleep(0.3)

    assert service.state()["state"] == "desconectado"
    assert FakeMultiUSRP.instances == []


def test_conectar_configura_o_usrp_como_pedido(running):
    service, _, _ = running(address="10.0.0.5", center_frequency_hz=437_200_000.0,
                            gain_db=20.0, antenna="TX/RX", clock_source="external")
    service.connect()

    assert wait_for(lambda: service.state()["state"] == "recebendo")
    usrp = last_device()
    assert usrp.args == "addr=10.0.0.5"
    assert usrp.freq_requests[0].target_freq == 437_200_000.0
    assert (usrp.gain, usrp.antenna, usrp.clock_source) == (20.0, "TX/RX", "external")
    assert usrp.streamer.stream_cmds[0].stream_now is True


def test_iq_sai_na_porta_como_o_grs_iq_rx_em_c(running):
    """Um lote por mensagem, UM frame só (sem tópico), complex64 intactos.
    Taxa pedida = taxa do cano, para não haver reamostragem no meio."""
    service, _, port = running(sample_rate_hz=250_000.0, output_rate_hz=250_000.0)
    context = zmq.Context()
    sub = context.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.setsockopt(zmq.RCVTIMEO, 3000)
    sub.connect(f"tcp://127.0.0.1:{port}")
    time.sleep(0.3)

    service.connect()
    assert wait_for(lambda: service.state()["state"] == "recebendo")
    samples = tone(1024)
    last_device().streamer.queue.append(samples)

    frames = sub.recv_multipart()
    sub.close(linger=0)
    context.term()

    assert len(frames) == 1
    assert np.array_equal(np.frombuffer(frames[0], dtype=np.complex64), samples)
    assert wait_for(lambda: service.state()["stats"]["blocks"] == 1)


def test_rádio_ausente_vira_erro_legivel_e_tenta_de_novo(running):
    FakeMultiUSRP.reachable = False
    service, _, _ = running()
    FakeMultiUSRP.reachable = False  # o fixture recria o módulo
    service.connect()

    assert wait_for(lambda: service.state()["state"] == "erro")
    state = service.state()
    assert "nenhum USRP respondeu em addr=192.168.10.2" in state["last_error"]

    FakeMultiUSRP.reachable = True  # o rádio aparece: a próxima tentativa conecta
    assert wait_for(lambda: service.state()["state"] == "recebendo", timeout=3)
    assert service.state()["last_error"] is None


def test_desconectar_para_o_stream(running):
    service, _, _ = running()
    service.connect()
    assert wait_for(lambda: service.state()["state"] == "recebendo")
    usrp = last_device()

    service.disconnect()

    assert wait_for(lambda: service.state()["state"] == "desconectado")
    assert [cmd.mode for cmd in usrp.streamer.stream_cmds] == ["start_cont", "stop_cont"]


def test_taxa_sem_razao_exata_com_o_cano_e_recusada(running):
    """Pedir 240 kS/s direto: o N210 entrega 100 MHz / 417 ≈ 239 808 S/s, e
    não há razão exata para 240 000. Publicar assim derrubaria ~65% dos
    pacotes no demodulador — então o serviço NÃO publica, e diz por quê."""
    service, _, _ = running(sample_rate_hz=240_000.0)
    service.connect()

    assert wait_for(lambda: service.state()["state"] == "erro")
    error = service.state()["last_error"]
    assert "239808" in error and "250000" in error
    # A primeira tentativa já foi encerrada (a thread segue tentando outras).
    assert FakeMultiUSRP.instances[0].streamer.stream_cmds[-1].mode == "stop_cont"


def test_250k_e_reamostrado_para_os_240k_do_cano(running):
    service, _, port = running()  # padrão: pede 250k, publica 240k
    context = zmq.Context()
    sub = context.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.setsockopt(zmq.RCVTIMEO, 3000)
    sub.connect(f"tcp://127.0.0.1:{port}")
    time.sleep(0.3)

    service.connect()
    assert wait_for(lambda: service.state()["state"] == "recebendo")
    for _ in range(25):
        last_device().streamer.queue.append(tone(8000))

    published = 0
    while published < 25 * 8000 * 24 // 25 - 64:
        published += len(np.frombuffer(sub.recv(), dtype=np.complex64))
    sub.close(linger=0)
    context.term()

    state = service.state()
    assert state["resampling"] == "24/25"
    assert state["warnings"] == []
    assert published == pytest.approx(25 * 8000 * 24 / 25, abs=64)


def test_nova_configuracao_reabre_a_sessao(running, tmp_path):
    service, _, port = running()
    service._config_path = tmp_path / "sdr.json"
    service.connect()
    assert wait_for(lambda: service.state()["state"] == "recebendo")

    new = SdrConfig.from_dict({**service.config.to_dict(),
                               "center_frequency_hz": 146_000_000.0})
    service.update_config(new)

    assert wait_for(lambda: len(FakeMultiUSRP.instances) == 2
                    and service.state()["state"] == "recebendo")
    assert last_device().freq_requests[0].target_freq == 146_000_000.0
    assert (tmp_path / "sdr.json").exists()


def test_antena_inexistente_e_erro_com_as_opcoes(running):
    service, _, _ = running(antenna="RX9")
    service.connect()

    assert wait_for(lambda: service.state()["state"] == "erro")
    assert "opções: TX/RX, RX2" in service.state()["last_error"]


def test_overflow_conta_e_nao_derruba(running):
    service, _, _ = running()
    service.connect()
    assert wait_for(lambda: service.state()["state"] == "recebendo")
    last_device().streamer.queue.extend(["overflow", tone(512)])

    assert wait_for(lambda: service.state()["stats"]["blocks"] == 1)
    assert service.state()["stats"]["overflows"] == 1
    assert service.state()["state"] == "recebendo"


def test_tune_pela_5557_move_a_sintonia(running):
    context = zmq.Context()
    pub = context.socket(zmq.PUB)
    tune_port = pub.bind_to_random_port("tcp://127.0.0.1")

    service, _, _ = running(tune_source=f"tcp://127.0.0.1:{tune_port}")
    service.connect()
    assert wait_for(lambda: service.state()["state"] == "recebendo")
    time.sleep(0.3)  # slow joiner

    ok = wait_for(lambda: (pub.send_multipart([b"tune", b"145903500"]) or True)
                  and service.state()["actual"]["center_frequency_hz"] == 145_903_500.0)
    pub.close(linger=0)
    context.term()

    assert ok


def test_probe_devolve_o_que_o_uhd_achou(running):
    service, _, _ = running()

    found = service.probe("192.168.10.2")

    assert found[0]["product"] == "N210r4"
    assert any("1 aparelho" in e["message"] for e in service.state()["events"])


def test_espectro_poe_o_pico_no_offset_do_sinal_depois_de_reamostrar(running):
    service, _, _ = running()  # 250k -> 240k
    service.connect()
    assert wait_for(lambda: service.state()["state"] == "recebendo")
    last_device().streamer.queue.append(tone(8192))  # 0,1 ciclo/amostra a 250k = +25 kHz
    assert wait_for(lambda: service.state()["stats"]["blocks"] == 1)

    db = service.spectrum_db(bins=1024)
    peak_hz = (int(np.argmax(db)) - 512) * 240_000 / 1024

    assert peak_hz == pytest.approx(25_000, abs=2 * 240_000 / 1024)
