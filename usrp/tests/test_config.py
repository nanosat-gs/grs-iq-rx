"""Configuração: o que é aceito, o que é recusado, e se sobrevive ao disco."""

from __future__ import annotations

import json

import pytest

from grs_iq_rx_usrp.config import SdrConfig, load_config, save_config


def test_padrao_e_o_n210_de_fabrica_na_beacon_do_fs1():
    config = SdrConfig()

    assert config.device_string() == "addr=192.168.10.2"
    assert config.center_frequency_hz == 145_900_000.0
    assert config.publish_bind == "tcp://*:5556"
    config.validate()


def test_argumentos_extras_entram_na_string_do_uhd():
    config = SdrConfig(address="10.0.0.5", device_args="recv_frame_size=1472")

    assert config.device_string() == "addr=10.0.0.5,recv_frame_size=1472"


def test_ida_e_volta_pelo_disco(tmp_path):
    path = tmp_path / "cfg" / "sdr.json"
    original = SdrConfig(address="10.0.0.5", gain_db=25.5, antenna="RX2", autoconnect=True)

    save_config(path, original)

    assert load_config(path) == original
    assert not path.with_suffix(".tmp").exists()


def test_sem_arquivo_vale_o_padrao(tmp_path):
    assert load_config(tmp_path / "nao-existe.json") == SdrConfig()


def test_sem_arquivo_o_tune_vem_do_padrao_do_compose(tmp_path):
    config = load_config(tmp_path / "nao-existe.json",
                         default_tune_source="tcp://grs-frequency-synthesizer:5557")

    assert config.tune_source == "tcp://grs-frequency-synthesizer:5557"


def test_arquivo_salvo_vence_o_padrao_mesmo_vazio(tmp_path):
    """O operador escolheu sintonia fixa no painel: o padrão do compose não
    pode religar o tune por baixo dele."""
    path = tmp_path / "sdr.json"
    save_config(path, SdrConfig(address="192.168.10.2", tune_source=""))

    config = load_config(path, default_tune_source="tcp://grs-frequency-synthesizer:5557")

    assert config.tune_source == ""


def test_padrao_de_tune_invalido_e_recusado(tmp_path):
    with pytest.raises(ValueError, match="tune_source"):
        load_config(tmp_path / "nao-existe.json", default_tune_source="localhost:5557")


def test_arquivo_com_campo_desconhecido_derruba_o_boot(tmp_path):
    path = tmp_path / "sdr.json"
    path.write_text(json.dumps({"adress": "10.0.0.5"}))  # typo de propósito

    with pytest.raises(ValueError, match="adress"):
        load_config(path)


def test_ganho_vazio_vira_none():
    assert SdrConfig.from_dict({"gain_db": ""}).gain_db is None
    assert SdrConfig.from_dict({"gain_db": None}).gain_db is None
    assert SdrConfig.from_dict({"gain_db": 30}).gain_db == 30.0


@pytest.mark.parametrize("data, message", [
    ({"address": ""}, "address"),
    ({"address": "10.0.0.5,type=b200"}, "address"),
    ({"device_args": "addr=10.0.0.9"}, "address"),
    ({"device_args": "semigual"}, "chave=valor"),
    ({"channel": -1}, "channel"),
    ({"channel": 1.5}, "inteiro"),
    ({"channel": True}, "inteiro"),
    ({"center_frequency_hz": 0}, "center_frequency_hz"),
    ({"sample_rate_hz": "240k"}, "número"),
    ({"gain_db": 500}, "gain_db"),
    ({"clock_source": "atomico"}, "clock_source"),
    ({"publish_bind": "5556"}, "publish_bind"),
    ({"tune_source": "localhost:5557"}, "tune_source"),
    ({"block_samples": 10}, "block_samples"),
    ({"output_rate_hz": 300_000}, "output_rate_hz"),
    ({"autoconnect": "sim"}, "true/false"),
    ([1, 2], "objeto"),
])
def test_configuracoes_invalidas_sao_recusadas(data, message):
    with pytest.raises(ValueError, match=message):
        SdrConfig.from_dict(data)
