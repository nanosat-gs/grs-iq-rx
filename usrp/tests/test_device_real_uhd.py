"""UsrpDevice contra o python3-uhd DE VERDADE.

Só o `MultiUSRP` — a classe que fala com o hardware — é duplo. TuneRequest,
StreamArgs, StreamCMD, StreamMode e RXMetadataErrorCode são o módulo real,
instalado via apt: se o código construir um deles errado, o teste falha contra
o tipo real, e não contra uma suposição sobre ele.

`RXMetadata` é a exceção, por motivo concreto: o `error_code` do tipo real
não tem setter do lado do Python (pybind11; só o recv() em C++ o preenche), e
sem hardware não há como simular overflow contra ele.

Pulado fora da imagem com python3-uhd (pytest.importorskip).
"""

from __future__ import annotations

import pytest

uhd = pytest.importorskip("uhd")

from fakes import FakeMultiUSRP, tone  # noqa: E402
from grs_iq_rx_usrp.config import SdrConfig  # noqa: E402
from grs_iq_rx_usrp.device import UsrpConnectionError, UsrpDevice, probe  # noqa: E402


class WritableMetadata:
    def __init__(self):
        self.error_code = uhd.types.RXMetadataErrorCode.none

    def strerror(self):
        return str(self.error_code)


@pytest.fixture(autouse=True)
def fake_hardware(monkeypatch):
    FakeMultiUSRP.instances.clear()
    FakeMultiUSRP.reachable = True
    FakeMultiUSRP.codes = uhd.types.RXMetadataErrorCode
    monkeypatch.setattr(uhd.usrp, "MultiUSRP", FakeMultiUSRP)
    monkeypatch.setattr(uhd.types, "RXMetadata", WritableMetadata)


def test_sintonia_e_um_tunerequest_real():
    UsrpDevice(SdrConfig(center_frequency_hz=145_900_000.0), uhd)

    request = FakeMultiUSRP.instances[-1].freq_requests[0]
    assert isinstance(request, uhd.types.TuneRequest)
    assert request.target_freq == 145_900_000.0


def test_stream_args_reais_pedem_fc32_sobre_sc16():
    UsrpDevice(SdrConfig(), uhd)

    args = FakeMultiUSRP.instances[-1].stream_args
    assert isinstance(args, uhd.usrp.StreamArgs)
    assert (args.cpu_format, args.otw_format) == ("fc32", "sc16")


def test_stream_continuo_e_parado_com_os_comandos_reais():
    device = UsrpDevice(SdrConfig(), uhd)
    device.close()

    cmds = FakeMultiUSRP.instances[-1].streamer.stream_cmds
    assert all(isinstance(c, uhd.types.StreamCMD) for c in cmds)
    assert cmds[0].stream_now is True
    assert len(cmds) == 2


def test_leitura_devolve_as_amostras_sem_conversao():
    device = UsrpDevice(SdrConfig(block_samples=4096), uhd)
    samples = tone(1000)
    FakeMultiUSRP.instances[-1].streamer.queue.append(samples)

    got, status = device.read()

    assert status == "ok"
    assert (got == samples).all()


def test_timeout_e_overflow_com_os_codigos_reais():
    device = UsrpDevice(SdrConfig(), uhd)
    FakeMultiUSRP.instances[-1].streamer.queue.append("overflow")

    assert device.read() == (None, "overflow")
    assert device.read() == (None, "timeout")


def test_endereco_sem_aparelho_vira_erro_do_proprio_bloco():
    FakeMultiUSRP.reachable = False

    with pytest.raises(UsrpConnectionError, match="addr=192.168.10.2"):
        UsrpDevice(SdrConfig(), uhd)


def test_find_real_sem_aparelho_devolve_lista_vazia(monkeypatch):
    """uhd.find de verdade, num IP de documentação (ninguém responde): ~0,5 s,
    lista vazia, sem exceção — é o que o botão "Testar conexão" mostra."""
    monkeypatch.undo()  # find não passa pelo duplo
    assert probe("192.0.2.1", uhd) == []
