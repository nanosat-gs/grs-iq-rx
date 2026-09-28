"""Duplos do UHD.

`FakeMultiUSRP` substitui só a ponta que fala com o hardware. Os testes de
aparelho (test_device_real_uhd.py) o combinam com os tipos REAIS do python3-uhd;
os de serviço e painel usam `fake_uhd_module()`, que também imita os tipos,
para rodarem sem o pacote.

O N210 é imitado no que importa para o serviço: a taxa pedida é ARREDONDADA
para 100 MHz / N, como o hardware faz.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

MASTER_CLOCK = 100e6


class FakeRange:
    def __init__(self, start, stop, step=0.0):
        self._v = (start, stop, step)

    def start(self):
        return self._v[0]

    def stop(self):
        return self._v[1]

    def step(self):
        return self._v[2]


class FakeStreamer:
    def __init__(self, codes) -> None:
        self._codes = codes
        self.stream_cmds: list = []
        self.queue: list = []  # np.ndarray ou nome de erro ("overflow", ...)

    def issue_stream_cmd(self, cmd) -> None:
        self.stream_cmds.append(cmd)

    def recv(self, buffer, metadata, timeout) -> int:
        if not self.queue:
            metadata.error_code = self._codes.timeout
            return 0
        item = self.queue.pop(0)
        if isinstance(item, str):
            metadata.error_code = getattr(self._codes, item)
            return 0
        buffer[0, : len(item)] = item
        metadata.error_code = self._codes.none
        return len(item)


class FakeMultiUSRP:
    instances: list["FakeMultiUSRP"] = []
    reachable = True
    codes = None  # preenchido por quem instala o duplo

    def __init__(self, args: str = "") -> None:
        if not FakeMultiUSRP.reachable:
            raise RuntimeError(f"LookupError: KeyError: No devices found for {args}")
        self.args = args
        self.rate = None
        self.freq_requests: list = []
        self.gain = 0.0
        self.antenna = "RX2"
        self.clock_source = None
        self.time_source = None
        self.streamer = FakeStreamer(FakeMultiUSRP.codes)
        FakeMultiUSRP.instances.append(self)

    def set_clock_source(self, source, mboard=0):
        self.clock_source = source

    def set_time_source(self, source, mboard=0):
        self.time_source = source

    def set_rx_rate(self, rate, chan=0):
        self.rate = MASTER_CLOCK / round(MASTER_CLOCK / rate)

    def get_rx_rate(self, chan=0):
        return self.rate

    def set_rx_freq(self, request, chan=0):
        self.freq_requests.append(request)

    def get_rx_freq(self, chan=0):
        return self.freq_requests[-1].target_freq

    def set_rx_gain(self, gain, chan=0):
        self.gain = gain

    def get_rx_gain(self, chan=0):
        return self.gain

    def get_rx_antennas(self, chan=0):
        return ["TX/RX", "RX2"]

    def set_rx_antenna(self, antenna, chan=0):
        self.antenna = antenna

    def get_rx_antenna(self, chan=0):
        return self.antenna

    def get_rx_gain_range(self, chan=0):
        return FakeRange(0.0, 31.5, 0.5)

    def get_rx_freq_range(self, chan=0):
        return FakeRange(50e6, 2.2e9)

    def get_mboard_name(self, mboard=0):
        return "N210r4"

    def get_rx_subdev_name(self, chan=0):
        return "WBXv3 RX+GDB"

    def get_rx_num_channels(self):
        return 1

    def get_clock_sources(self, mboard):
        return ["internal", "external", "mimo"]

    def get_time_sources(self, mboard):
        return ["none", "external", "mimo"]

    def get_rx_sensor_names(self, chan=0):
        return ["lo_locked"]

    def get_rx_stream(self, stream_args):
        self.stream_args = stream_args
        return self.streamer


def fake_uhd_module(found: list[dict] | None = None):
    """Um módulo `uhd` de mentira, com a forma que o serviço usa."""
    codes = SimpleNamespace(none="none", timeout="timeout", overflow="overflow",
                            bad_packet="bad_packet")

    class TuneRequest:
        def __init__(self, target_freq):
            self.target_freq = target_freq

    class StreamArgs:
        def __init__(self, cpu_format, otw_format):
            self.cpu_format = cpu_format
            self.otw_format = otw_format
            self.channels = [0]

    class StreamCMD:
        def __init__(self, mode):
            self.mode = mode
            self.stream_now = False

    class RXMetadata:
        def __init__(self):
            self.error_code = codes.none

        def strerror(self):
            return f"ERROR_CODE_{str(self.error_code).upper()}"

    class DeviceAddr:
        def __init__(self, values):
            self._values = values

        def to_dict(self):
            return dict(self._values)

    FakeMultiUSRP.codes = codes
    FakeMultiUSRP.instances.clear()
    FakeMultiUSRP.reachable = True

    return SimpleNamespace(
        usrp=SimpleNamespace(MultiUSRP=FakeMultiUSRP, StreamArgs=StreamArgs),
        types=SimpleNamespace(
            TuneRequest=TuneRequest, StreamCMD=StreamCMD, RXMetadata=RXMetadata,
            RXMetadataErrorCode=codes,
            StreamMode=SimpleNamespace(start_cont="start_cont", stop_cont="stop_cont"),
        ),
        find=lambda args: [DeviceAddr(d) for d in (found or [])],
    )


def tone(n: int = 4096) -> np.ndarray:
    return np.exp(1j * 2 * np.pi * 0.1 * np.arange(n)).astype(np.complex64)
