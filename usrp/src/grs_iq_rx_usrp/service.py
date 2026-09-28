"""O serviço: mantém a sessão com o USRP e publica o IQ na :5556.

Uma thread de trabalho é dona de tudo que não é thread-safe — a sessão UHD e
o socket PUB. O painel não toca em nenhum dos dois: ele pede (conectar,
desconectar, nova configuração) e a thread executa entre dois recv().

Sem o rádio, o serviço fica DE PÉ e diz por quê não conectou. O `grs-iq-rx`
em C sai com EXIT_FAILURE sem dongle; aqui isso deixaria o operador sem
painel justamente quando precisa dele — para corrigir o IP.
"""

from __future__ import annotations

import logging
import pathlib
import threading
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np
import zmq

from grs_iq_rx_usrp.config import SdrConfig, save_config
from grs_iq_rx_usrp.device import UsrpConnectionError, UsrpDevice, probe
from grs_iq_rx_usrp.resample import RationalResampler, exact_ratio

logger = logging.getLogger(__name__)

RETRY_INTERVAL_S = 10.0
TUNE_TOPIC = b"tune"
SPECTRUM_BINS = 1024
EVENTS_KEPT = 40


class SdrService:
    def __init__(self, config: SdrConfig, uhd, config_path: pathlib.Path | None = None,
                 retry_interval_s: float = RETRY_INTERVAL_S) -> None:
        self._uhd = uhd
        self._config = config
        self._config_path = config_path
        self._retry_interval_s = retry_interval_s

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._shutdown = threading.Event()
        self._want_running = config.autoconnect
        self._restart = False

        self._state = "desconectado"
        self._last_error: str | None = None
        self._actual: dict | None = None
        self._info: dict | None = None
        self._events: deque[dict] = deque(maxlen=EVENTS_KEPT)
        self._stats = _Stats()
        self._last_block: np.ndarray | None = None
        self._pending_tune: float | None = None
        self._resampler: RationalResampler | None = None
        self._resampling: str | None = None

        self._worker = threading.Thread(target=self._run, daemon=True, name="usrp-worker")
        self._tuner: _TuneListener | None = None

    # --- ciclo de vida --------------------------------------------------------

    def start(self) -> None:
        self._worker.start()
        self._restart_tuner()
        if self._config.autoconnect:
            self._event("conexão automática ligada")

    def stop(self) -> None:
        self._shutdown.set()
        self._wake.set()
        self._worker.join(timeout=5)
        if self._tuner is not None:
            self._tuner.stop()

    # --- pedidos do painel ----------------------------------------------------

    def connect(self) -> None:
        with self._lock:
            self._want_running = True
            self._restart = True
        self._wake.set()

    def disconnect(self) -> None:
        with self._lock:
            self._want_running = False
            self._restart = True
        self._wake.set()

    def update_config(self, config: SdrConfig) -> None:
        """Salva e, se estiver recebendo, reconecta com a configuração nova."""
        if self._config_path is not None:
            save_config(self._config_path, config)

        with self._lock:
            tune_changed = config.tune_source != self._config.tune_source
            self._config = config
            self._restart = True
        self._event("configuração salva")
        if tune_changed:
            self._restart_tuner()
        self._wake.set()

    def probe(self, address: str | None = None) -> list[dict]:
        target = (address or self._config.address).strip()
        found = probe(target, self._uhd)
        self._event(f"teste de conexão em {target}: "
                    + (f"{len(found)} aparelho(s)" if found else "ninguém respondeu"))
        return found

    def request_tune(self, frequency_hz: float) -> None:
        with self._lock:
            self._pending_tune = frequency_hz

    # --- leitura para o painel --------------------------------------------------

    @property
    def config(self) -> SdrConfig:
        with self._lock:
            return self._config

    def state(self) -> dict:
        with self._lock:
            config = self._config
            actual = self._actual
            warnings = []
            if actual is not None:
                if abs(actual["sample_rate_hz"] - config.sample_rate_hz) > 0.5:
                    warnings.append(
                        f"taxa pedida {config.sample_rate_hz:.0f} S/s, o USRP entrega "
                        f"{actual['sample_rate_hz']:.1f} S/s (arredondou para a grade dele)"
                    )
                if abs(actual["center_frequency_hz"] - config.center_frequency_hz) > 1.0:
                    warnings.append(
                        f"sintonia real {actual['center_frequency_hz']:.0f} Hz difere da "
                        f"pedida {config.center_frequency_hz:.0f} Hz"
                    )

            return {
                "state": self._state,
                "want_running": self._want_running,
                "last_error": self._last_error,
                "config": config.to_dict(),
                "device_string": config.device_string(),
                "actual": actual,
                "output_rate_hz": config.output_rate_hz,
                "resampling": self._resampling,
                "info": self._info,
                "warnings": warnings,
                "stats": self._stats.snapshot(),
                "events": list(self._events),
            }

    def spectrum_db(self, bins: int = SPECTRUM_BINS) -> list[float]:
        with self._lock:
            block = self._last_block
        if block is None or len(block) < bins:
            return []

        segments = len(block) // bins
        frames = block[: segments * bins].reshape(segments, bins)
        window = np.hanning(bins).astype(np.float32)
        power = np.mean(np.abs(np.fft.fft(frames * window, axis=1)) ** 2, axis=0)
        power = np.fft.fftshift(power) / (bins * float(np.sum(window ** 2)))

        return np.round(10.0 * np.log10(power + 1e-20), 1).tolist()

    # --- a thread de trabalho ---------------------------------------------------

    def _run(self) -> None:
        context = zmq.Context()
        publisher = None
        bound_to = None
        device: UsrpDevice | None = None

        while not self._shutdown.is_set():
            with self._lock:
                config = self._config
                want = self._want_running
                restart = self._restart
                self._restart = False
                tune = self._pending_tune
                self._pending_tune = None

            if restart and device is not None:
                device.close()
                device = None
                self._set_state("desconectado", actual=None)
                self._event("sessão encerrada")

            if publisher is None or bound_to != config.publish_bind:
                if publisher is not None:
                    publisher.close(linger=0)
                publisher = context.socket(zmq.PUB)
                publisher.bind(config.publish_bind)
                bound_to = config.publish_bind
                self._event(f"publicando IQ em {bound_to}")

            if not want:
                with self._lock:
                    self._state = "desconectado"
                self._wake.wait(0.5)
                self._wake.clear()
                continue

            if device is None:
                device = self._try_connect(config)
                if device is None:
                    self._wake.wait(self._retry_interval_s)
                    self._wake.clear()
                continue

            if tune is not None:
                try:
                    device.retune(tune)
                    with self._lock:
                        self._actual = device.actual()
                    self._event(f"tune -> {tune / 1e6:.4f} MHz")
                except Exception as error:  # noqa: BLE001 — um tune ruim não derruba a recepção
                    self._event(f"tune recusado: {error}")

            samples, status = device.read()
            if status == "ok":
                if self._resampler is not None:
                    samples = self._resampler.process(samples)
                if len(samples):
                    publisher.send(samples.tobytes())
                    with self._lock:
                        self._last_block = samples
                    self._stats.add(len(samples))
            elif status == "overflow":
                self._stats.overflow()
            elif status != "timeout":
                self._stats.error()
                self._event(f"erro de recepção: {status}")

        if device is not None:
            device.close()
        if publisher is not None:
            publisher.close(linger=0)
        context.term()

    def _try_connect(self, config: SdrConfig) -> UsrpDevice | None:
        self._set_state("conectando")
        self._event(f"conectando em {config.device_string()}")
        try:
            device = UsrpDevice(config, self._uhd)
            actual = device.actual()
            info = device.info()
            ratio = exact_ratio(config.output_rate_hz, actual["sample_rate_hz"])
            if ratio is None:
                device.close()
                raise UsrpConnectionError(
                    f"o USRP entrega {actual['sample_rate_hz']:.3f} S/s, e não há razão "
                    f"exata que leve isso a {config.output_rate_hz:.0f} S/s (a taxa do "
                    f"cano). Publicar assim quebraria o demodulador — 0,08% de erro de "
                    f"taxa já derruba ~65% dos pacotes. Peça uma taxa da grade do "
                    f"N210 (100 MHz / N) com razão exata, ex.: 250000."
                )
        except (UsrpConnectionError, RuntimeError) as error:
            with self._lock:
                self._state = "erro"
                self._last_error = str(error)
                self._actual = None
            self._event(f"falhou: {error}")
            return None

        if ratio == 1:
            resampler, resampling = None, None
        else:
            resampler = RationalResampler(ratio.numerator, ratio.denominator)
            resampling = f"{ratio.numerator}/{ratio.denominator}"

        with self._lock:
            self._state = "recebendo"
            self._last_error = None
            self._actual = actual
            self._info = info
            self._resampler = resampler
            self._resampling = resampling
        self._stats.reset()
        self._event(f"conectado: {info['mboard']} ({info['subdev']}), "
                    f"{actual['sample_rate_hz']:.1f} S/s"
                    + (f", reamostrando {resampling} -> {config.output_rate_hz:.0f} S/s"
                       if resampling else ""))
        return device

    def _set_state(self, state: str, **fields) -> None:
        with self._lock:
            self._state = state
            if "actual" in fields:
                self._actual = fields["actual"]

    def _event(self, message: str) -> None:
        logger.info(message)
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with self._lock:
            self._events.appendleft({"at": stamp, "message": message})

    def _restart_tuner(self) -> None:
        if self._tuner is not None:
            self._tuner.stop()
            self._tuner = None
        address = self.config.tune_source
        if address:
            self._tuner = _TuneListener(address, self.request_tune)
            self._tuner.start()
            self._event(f"ouvindo tune em {address}")


class _Stats:
    """Contadores de recepção, com taxa MEDIDA (amostras/s no último ~1 s)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._blocks = 0
            self._samples = 0
            self._overflows = 0
            self._errors = 0
            self._window: deque[tuple[float, int]] = deque()

    def add(self, samples: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._blocks += 1
            self._samples += samples
            self._window.append((now, samples))
            while self._window and now - self._window[0][0] > 1.0:
                self._window.popleft()

    def overflow(self) -> None:
        with self._lock:
            self._overflows += 1

    def error(self) -> None:
        with self._lock:
            self._errors += 1

    def snapshot(self) -> dict:
        now = time.monotonic()
        with self._lock:
            recent = [n for at, n in self._window if now - at <= 1.0]
            return {
                "blocks": self._blocks,
                "samples": self._samples,
                "overflows": self._overflows,
                "errors": self._errors,
                "measured_rate": float(sum(recent)),
            }


class _TuneListener(threading.Thread):
    """Assina `tune` na :5557 — o mesmo contrato do grs-sdr-sim e do grs-iq-rx
    em C. Só entrega o pedido; quem chama o USRP é a thread de trabalho."""

    def __init__(self, address: str, deliver) -> None:
        super().__init__(daemon=True, name="usrp-tune")
        self._address = address
        self._deliver = deliver
        self._stopping = threading.Event()

    def run(self) -> None:
        context = zmq.Context()
        socket = context.socket(zmq.SUB)
        socket.setsockopt(zmq.SUBSCRIBE, TUNE_TOPIC)
        socket.setsockopt(zmq.RCVTIMEO, 500)
        socket.connect(self._address)

        while not self._stopping.is_set():
            try:
                frames = socket.recv_multipart()
            except zmq.Again:
                continue
            except zmq.ZMQError:
                break
            if len(frames) != 2:
                continue
            try:
                frequency = float(frames[1].decode())
            except (ValueError, UnicodeDecodeError):
                logger.warning("tune ilegível: %r", frames[1])
                continue
            if frequency > 0:
                self._deliver(frequency)

        socket.close(linger=0)
        context.term()

    def stop(self) -> None:
        self._stopping.set()
