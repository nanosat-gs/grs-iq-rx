"""Reamostragem racional exata, em fluxo, para entregar a taxa do cano.

O USRP N210 só gera 100 MHz / N. A taxa do cano é 240 kS/s (50 amostras por
símbolo a 4800 baud), que NÃO está nessa grade: o UHD arredonda o pedido sem
erro nenhum. Medido na bancada do demodulador: o demodulador configurado a
240 kS/s recebendo 239,8 kS/s (0,08% de diferença) entrega ~35% dos pacotes
íntegros; a 0,16%, 3%.

Por isso o bloco pede uma taxa EXATA no N210 (250 kS/s = 100 MHz / 400) e
reamostra por uma razão racional exata (24/25) para publicar 240 kS/s. O
contrato da :5556 fica igual para rádio, simulador e replay.

Polifásico, numpy puro, com estado entre lotes: processar o fluxo em pedaços
dá exatamente o mesmo resultado que processá-lo inteiro — a mesma regra que o
demodulador passou a seguir depois de perder um pacote em quatro nas
fronteiras de janela.
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np

TAPS_PER_PHASE = 48
KAISER_BETA = 8.0
MAX_DENOMINATOR = 1000


def exact_ratio(output_rate: float, input_rate: float) -> Fraction | None:
    """up/down com output = input * up / down EXATO, ou None se não houver
    razão pequena (denominador <= 1000) que acerte a taxa."""
    ratio = Fraction(output_rate / input_rate).limit_denominator(MAX_DENOMINATOR)
    if abs(float(ratio) * input_rate - output_rate) > 1e-9 * output_rate:
        return None
    return ratio


class RationalResampler:
    """y = x reamostrado por up/down, com filtro anti-alias.

    Sinal complexo (complex64). A banda útil é preservada até ~83% da
    Nyquist de saída; o que passa disso é atenuado antes de dobrar.
    """

    def __init__(self, up: int, down: int) -> None:
        if up <= 0 or down <= 0:
            raise ValueError("up e down precisam ser positivos")
        self.up = up
        self.down = down

        length = TAPS_PER_PHASE * up
        # Corte na menor das duas Nyquist, com margem, na taxa sobreamostrada.
        cutoff = 0.5 / max(up, down) * 0.83
        n = np.arange(length) - (length - 1) / 2.0
        taps = 2 * cutoff * np.sinc(2 * cutoff * n) * np.kaiser(length, KAISER_BETA)
        taps *= up / np.sum(taps)  # ganho de passagem 1 depois de inserir zeros

        # polyphase[p, m] = taps[p + m*up], invertido em m para casar com a
        # janela deslizante (que vem em ordem crescente de tempo).
        self._phases = taps.reshape(TAPS_PER_PHASE, up).T[:, ::-1].astype(np.complex64)
        self._history = np.zeros(TAPS_PER_PHASE - 1, dtype=np.complex64)
        self._consumed = 0   # amostras de entrada já vistas (índice global)
        self._next_out = 0   # índice global da próxima saída

    def process(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.complex64)
        if len(block) == 0:
            return block

        taps = TAPS_PER_PHASE
        start = self._consumed - (taps - 1)          # índice global de xx[0]
        xx = np.concatenate((self._history, block))
        end_input = self._consumed + len(block)      # primeiro índice NÃO visto

        # Maior k com floor(k*down/up) <= end_input - 1.
        last = (end_input * self.up - 1) // self.down
        ks = np.arange(self._next_out, last + 1, dtype=np.int64)

        if len(ks):
            n = ks * self.down
            i = n // self.up
            p = n % self.up
            windows = np.lib.stride_tricks.sliding_window_view(xx, taps)
            out = np.sum(self._phases[p] * windows[i - start - (taps - 1)], axis=1)
        else:
            out = np.zeros(0, dtype=np.complex64)

        self._next_out = int(last) + 1
        self._consumed = end_input
        self._history = xx[-(taps - 1):].copy()

        return out.astype(np.complex64)
