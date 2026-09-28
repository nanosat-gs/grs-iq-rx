"""O USRP em si, via python3-uhd: conectar, configurar, descrever, receber.

Camada fina sobre o UHD, de propósito. A Ettus não documenta o protocolo de
rede do USRP para uso por terceiros; o caminho suportado é a biblioteca deles
— a mesma `uhd.usrp.MultiUSRP` que o capítulo de USRP do PySDR usa.

## O que foi confirmado sem o rádio, e como

Contra o `python3-uhd` 4.3.0.0 do Debian bookworm, instalado num container:
a API de cada chamada usada aqui (inspecionada, não copiada da documentação,
que é de uma versão mais nova e já diverge); `uhd.find("addr=<ip>")` responde
em ~0,5 s sem aparelho; `MultiUSRP("addr=<ip>")` falha em ~0,7 s com
RuntimeError, sem travar.

O que exige o N210 físico e NÃO foi confirmado: se o recv() entrega amostras,
se set_rx_freq sintoniza, o comportamento com a imagem de FPGA deste rádio.

## A taxa pedida não é a taxa entregue

O N210 deriva a taxa de um relógio de 100 MHz dividido por um inteiro. Um
pedido que não cai nessa grade é ARREDONDADO pelo UHD, sem erro — 240 kS/s
vira ~240,38 kS/s. Por isso a taxa efetiva é lida de volta e exposta: é ela
que o demodulador precisa usar, ou o sincronismo trabalha com o número errado
de amostras por símbolo.
"""

from __future__ import annotations

import logging

import numpy as np

from grs_iq_rx_usrp.config import SdrConfig

logger = logging.getLogger(__name__)

DEFAULT_RECV_TIMEOUT_S = 0.5


class UsrpConnectionError(RuntimeError):
    """`uhd` ausente, ou o USRP não respondeu / recusou a configuração."""


def import_uhd():
    """Import tardio: `uhd` só existe na imagem com python3-uhd (via apt; o
    PyPI só tem wheel para Windows). Os testes de serviço e de painel rodam
    sem ele, com um duplo."""
    try:
        import uhd
    except ImportError as error:
        raise UsrpConnectionError(
            "módulo 'uhd' não encontrado — este serviço roda na imagem com "
            "python3-uhd (docker/grs-iq-rx-usrp.Dockerfile)"
        ) from error

    return uhd


def probe(address: str, uhd) -> list[dict]:
    """Procura um USRP no endereço, sem abrir sessão nem parar um stream.

    Lista vazia = ninguém respondeu naquele IP.
    """
    return [found.to_dict() for found in uhd.find(f"addr={address.strip()}")]


class UsrpDevice:
    """Uma sessão aberta e recebendo. `close()` encerra."""

    def __init__(self, config: SdrConfig, uhd) -> None:
        self._uhd = uhd
        self.config = config
        channel = config.channel

        try:
            self._usrp = uhd.usrp.MultiUSRP(config.device_string())
        except RuntimeError as error:
            raise UsrpConnectionError(
                f"nenhum USRP respondeu em {config.device_string()}: {error}"
            ) from error

        try:
            self._usrp.set_clock_source(config.clock_source)
            self._usrp.set_time_source(config.time_source)
            self._usrp.set_rx_rate(config.sample_rate_hz, channel)
            self._usrp.set_rx_freq(uhd.types.TuneRequest(config.center_frequency_hz), channel)
            if config.gain_db is not None:
                self._usrp.set_rx_gain(config.gain_db, channel)
            if config.antenna:
                available = list(self._usrp.get_rx_antennas(channel))
                if config.antenna not in available:
                    raise UsrpConnectionError(
                        f"antena {config.antenna!r} não existe neste USRP; "
                        f"opções: {', '.join(available)}"
                    )
                self._usrp.set_rx_antenna(config.antenna, channel)

            stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
            stream_args.channels = [channel]
            self._streamer = self._usrp.get_rx_stream(stream_args)
        except UsrpConnectionError:
            raise
        except RuntimeError as error:
            raise UsrpConnectionError(f"o USRP recusou a configuração: {error}") from error

        self._metadata = uhd.types.RXMetadata()
        # (1, N): a forma do exemplo oficial (buffer indexado por canal).
        self._buffer = np.zeros((1, config.block_samples), dtype=np.complex64)

        start = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
        start.stream_now = True
        self._streamer.issue_stream_cmd(start)
        self._closed = False

    # --- o que o aparelho de fato aceitou -------------------------------------

    def actual(self) -> dict:
        channel = self.config.channel

        return {
            "sample_rate_hz": float(self._usrp.get_rx_rate(channel)),
            "center_frequency_hz": float(self._usrp.get_rx_freq(channel)),
            "gain_db": float(self._usrp.get_rx_gain(channel)),
            "antenna": str(self._usrp.get_rx_antenna(channel)),
        }

    def info(self) -> dict:
        """O que o painel mostra do aparelho: modelo, faixas, opções."""
        channel = self.config.channel
        gain = self._usrp.get_rx_gain_range(channel)
        freq = self._usrp.get_rx_freq_range(channel)

        return {
            "mboard": str(self._usrp.get_mboard_name()),
            "subdev": str(self._usrp.get_rx_subdev_name(channel)),
            "channels": int(self._usrp.get_rx_num_channels()),
            "antennas": [str(a) for a in self._usrp.get_rx_antennas(channel)],
            "gain_range_db": [gain.start(), gain.stop(), gain.step()],
            "freq_range_hz": [freq.start(), freq.stop()],
            "clock_sources": [str(s) for s in self._usrp.get_clock_sources(0)],
            "time_sources": [str(s) for s in self._usrp.get_time_sources(0)],
            "sensors": [str(s) for s in self._usrp.get_rx_sensor_names(channel)],
        }

    # --- recepção -------------------------------------------------------------

    def read(self, timeout_s: float = DEFAULT_RECV_TIMEOUT_S) -> tuple[np.ndarray | None, str]:
        """Um recv(). Devolve (amostras, "ok") ou (None, motivo).

        Motivos: "timeout" (nada nesta janela — sem sinal não é erro),
        "overflow" (o host não drenou a tempo; amostras perdidas), ou a
        mensagem do UHD para qualquer outro erro.
        """
        codes = self._uhd.types.RXMetadataErrorCode
        n = self._streamer.recv(self._buffer, self._metadata, timeout_s)
        code = self._metadata.error_code

        if code == codes.timeout:
            return None, "timeout"
        if code == codes.overflow:
            return None, "overflow"
        if code != codes.none:
            return None, str(self._metadata.strerror())
        if n == 0:
            return None, "timeout"

        return self._buffer[0, :n].copy(), "ok"

    def retune(self, frequency_hz: float) -> None:
        self._usrp.set_rx_freq(self._uhd.types.TuneRequest(frequency_hz), self.config.channel)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            stop = self._uhd.types.StreamCMD(self._uhd.types.StreamMode.stop_cont)
            self._streamer.issue_stream_cmd(stop)
        except Exception:
            logger.exception("falha ao emitir stop_cont")
        # MultiUSRP não tem close() na API inspecionada: a sessão é liberada
        # quando o objeto é coletado.
        self._streamer = None
        self._usrp = None
