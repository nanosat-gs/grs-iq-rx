"""O que é preciso para conectar num USRP e receber, e onde isso fica guardado.

A configuração vive num arquivo JSON num volume, e não em variáveis de
ambiente: ela é editada pelo painel com o serviço rodando, e tem de
sobreviver a um restart do container sem que alguém redigite o IP do rádio.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
from dataclasses import asdict, dataclass, fields

CLOCK_SOURCES = ("internal", "external", "mimo", "gpsdo")


@dataclass
class SdrConfig:
    # IP do USRP na rede. O N210 sai de fábrica em 192.168.10.2.
    address: str = "192.168.10.2"
    # Argumentos extras do UHD, "chave=valor,chave=valor" — ex.:
    # recv_frame_size=1472 quando o caminho de rede não aguenta jumbo frame.
    device_args: str = ""
    channel: int = 0
    # Vazio = a antena padrão da daughterboard. As opções reais só são
    # conhecidas depois de conectar (o painel as lista).
    antenna: str = ""
    center_frequency_hz: float = 145_900_000.0
    # Taxa PEDIDA ao USRP. 250 kS/s é exata no N210 (100 MHz / 400); a taxa
    # do cano (240 kS/s) não é, e o UHD a arredondaria sem avisar.
    sample_rate_hz: float = 250_000.0
    # Taxa PUBLICADA na :5556 — o contrato do cano (50 amostras por símbolo a
    # 4800 baud). O serviço reamostra por uma razão exata (250k -> 240k =
    # 24/25) e recusa conectar se a taxa entregue não tiver razão exata.
    output_rate_hz: float = 240_000.0
    # None = não mexer no ganho (o que a daughterboard estiver usando).
    gain_db: float | None = None
    clock_source: str = "internal"
    time_source: str = "internal"
    # Onde o IQ sai: mesmo contrato do grs-iq-rx em C — PUB, um lote por
    # mensagem, sem tópico, cf32_le.
    publish_bind: str = "tcp://*:5556"
    # PUB do frequency-synthesizer (`tune`). Vazio = sintonia fixa.
    tune_source: str = ""
    block_samples: int = 8192
    # Conectar sozinho ao subir o container (e tentar de novo se cair).
    autoconnect: bool = False

    def device_string(self) -> str:
        parts = [f"addr={self.address.strip()}"]
        if self.device_args.strip():
            parts.append(self.device_args.strip())
        return ",".join(parts)

    def validate(self) -> None:
        if not self.address.strip():
            raise ValueError("address: informe o IP do USRP")
        if any(ch in self.address for ch in ",= "):
            raise ValueError("address: só o IP ou o nome, sem vírgula, espaço ou '='")
        for pair in filter(None, (p.strip() for p in self.device_args.split(","))):
            if "=" not in pair:
                raise ValueError(f"device_args: '{pair}' não está no formato chave=valor")
            if pair.split("=", 1)[0].strip() == "addr":
                raise ValueError("device_args: o endereço vai no campo address")
        if self.channel < 0:
            raise ValueError("channel: precisa ser >= 0")
        if self.center_frequency_hz <= 0:
            raise ValueError("center_frequency_hz: precisa ser positivo")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz: precisa ser positivo")
        if self.output_rate_hz <= 0:
            raise ValueError("output_rate_hz: precisa ser positivo")
        if self.output_rate_hz > self.sample_rate_hz:
            raise ValueError("output_rate_hz: não pode passar da taxa pedida ao USRP "
                             "(reamostrar para cima não cria banda)")
        if self.gain_db is not None and not 0 <= self.gain_db <= 100:
            raise ValueError("gain_db: fora de [0, 100] dB")
        for name in ("clock_source", "time_source"):
            if getattr(self, name) not in CLOCK_SOURCES:
                raise ValueError(f"{name}: use um de {', '.join(CLOCK_SOURCES)}")
        if not self.publish_bind.startswith("tcp://"):
            raise ValueError("publish_bind: esperava tcp://...")
        if self.tune_source and not self.tune_source.startswith("tcp://"):
            raise ValueError("tune_source: esperava tcp://... ou vazio")
        if not 256 <= self.block_samples <= 1_000_000:
            raise ValueError("block_samples: fora de [256, 1000000]")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SdrConfig":
        """Estrito: campo desconhecido ou de tipo errado é erro, não é ignorado.

        Um typo no nome de um campo que fosse ignorado em silêncio deixaria o
        rádio com o valor antigo enquanto o operador acha que mudou.
        """
        if not isinstance(data, dict):
            raise ValueError("esperava um objeto JSON")

        known = {f.name: f for f in fields(cls)}
        unknown = set(data) - set(known)
        if unknown:
            raise ValueError(f"campo desconhecido: {', '.join(sorted(unknown))}")

        values = {}
        for name, value in data.items():
            values[name] = _coerce(name, value, cls.__dataclass_fields__[name].default)

        config = cls(**{**asdict(cls()), **values})
        config.validate()
        return config


def _coerce(name: str, value, default):
    if name == "gain_db":
        if value is None or value == "":
            return None
        return _number(name, value)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{name}: esperava true/false")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name}: esperava um inteiro")
        return value
    if isinstance(default, float):
        return _number(name, value)
    if not isinstance(value, str):
        raise ValueError(f"{name}: esperava texto")
    return value.strip()


def _number(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: esperava um número")
    if not math.isfinite(value):
        raise ValueError(f"{name}: precisa ser finito")
    return float(value)


def load_config(path: pathlib.Path, default_tune_source: str = "",
                default_center_frequency_hz: float | None = None) -> SdrConfig:
    """O que está salvo, ou o padrão se nada foi salvo ainda.

    Os `default_*` só valem no padrão: é como o compose liga cada receptor ao
    sintetizador do seu rádio e o sintoniza na faixa certa (o N210 do UHF não
    pode nascer em 145,9 MHz) sem o operador precisar saber disso. Depois do
    primeiro "Salvar" no painel, manda o que está no arquivo — inclusive
    `tune_source` vazio, se o operador quis sintonia fixa.
    """
    if not path.exists():
        default_tune_source = default_tune_source.strip()
        if default_tune_source and not default_tune_source.startswith("tcp://"):
            raise ValueError("tune_source padrão: esperava tcp://... ou vazio")
        config = SdrConfig(tune_source=default_tune_source)
        if default_center_frequency_hz is not None:
            if default_center_frequency_hz <= 0:
                raise ValueError("center_frequency_hz padrão: precisa ser positivo")
            config = SdrConfig(tune_source=default_tune_source,
                               center_frequency_hz=float(default_center_frequency_hz))
        return config

    return SdrConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_config(path: pathlib.Path, config: SdrConfig) -> None:
    """Escrita atômica: um container derrubado no meio da gravação não pode
    deixar um JSON pela metade, que impediria o serviço de subir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
    os.replace(temporary, path)
