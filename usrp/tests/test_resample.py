"""O reamostrador: taxa exata, sinal preservado, e em fluxo sem costura."""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from grs_iq_rx_usrp.resample import RationalResampler, exact_ratio


def tone(freq_hz, rate, n):
    return np.exp(2j * np.pi * freq_hz / rate * np.arange(n)).astype(np.complex64)


def peak_hz(samples, rate):
    spectrum = np.abs(np.fft.fft(samples * np.hanning(len(samples))))
    freqs = np.fft.fftfreq(len(samples), 1 / rate)
    return freqs[int(np.argmax(spectrum))]


def test_250k_para_240k_e_24_25_exato():
    assert exact_ratio(240_000, 250_000) == Fraction(24, 25)


def test_taxa_fora_da_grade_nao_tem_razao_exata():
    """100 MHz / 417: sem razão pequena que dê 240 kS/s — o serviço recusa."""
    assert exact_ratio(240_000, 100e6 / 417) is None


def test_saem_24_amostras_para_cada_25():
    resampler = RationalResampler(24, 25)
    out = sum(len(resampler.process(np.zeros(8192, np.complex64))) for _ in range(25))

    assert out == pytest.approx(8192 * 24, abs=1)


def test_tom_fica_na_mesma_frequencia_e_amplitude():
    resampler = RationalResampler(24, 25)
    out = resampler.process(tone(20_000, 250_000, 250_000))[1000:]  # pula o transiente

    assert peak_hz(out, 240_000) == pytest.approx(20_000, abs=240_000 / len(out) * 2)
    assert np.mean(np.abs(out)) == pytest.approx(1.0, rel=0.01)


def test_em_pedacos_e_igual_a_de_uma_vez():
    """A regra que o demodulador aprendeu a duras penas: a divisão em lotes
    não pode mudar o resultado."""
    rng = np.random.default_rng(3)
    x = (rng.standard_normal(60_000) + 1j * rng.standard_normal(60_000)).astype(np.complex64)

    whole = RationalResampler(24, 25).process(x)
    chunked_resampler = RationalResampler(24, 25)
    parts, start = [], 0
    for size in [8192, 1, 3000, 777, 20000, 5] * 20:
        if start >= len(x):
            break
        parts.append(chunked_resampler.process(x[start : start + size]))
        start += size

    assert np.allclose(np.concatenate(parts), whole, atol=1e-5)


def test_sinal_perto_da_borda_da_banda_de_saida_e_atenuado():
    """Um tom a 123 kHz (acima da Nyquist de saída, 120 kHz) dobraria para
    dentro da banda; o filtro tem de derrubá-lo."""
    resampler = RationalResampler(24, 25)
    out = resampler.process(tone(123_000, 250_000, 100_000))[2000:]

    assert np.mean(np.abs(out)) < 0.05
