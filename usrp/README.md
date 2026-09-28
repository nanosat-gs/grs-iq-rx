# grs-iq-rx / usrp — receptor USRP N210 (python3-uhd)

Receptor do USRP N210 da estação nanosat-gs, com painel de configuração.
Mesmo contrato do receptor em C deste repositório, então demodulador e
gravador não sabem qual dos dois está publicando:

```
USRP N210 ──UDP/GigE (UHD)──▶ UsrpDevice ──▶ reamostra 250k→240k ──▶ PUB :5556 (cf32_le, sem tópico)
                                   ▲
            SUB :5557 "tune" ──────┘          painel HTTP :8091
```

## Por que python3-uhd

A Ettus não documenta o protocolo de rede do USRP para uso por terceiros; o
caminho suportado é a biblioteca deles, o UHD. `python3-uhd` é a mesma API
(`uhd.usrp.MultiUSRP`) que o capítulo de USRP do [PySDR](https://pysdr.org)
usa. Ela só existe via apt no Linux (o PyPI só tem wheel para Windows), por
isso a imagem é Debian puro — ver `docker/grs-iq-rx-usrp.Dockerfile` no
`grs-station`.

## A taxa: pedida ao rádio ≠ publicada no cano

O N210 só gera 100 MHz / N. A taxa do cano, 240 kS/s (50 amostras por
símbolo a 4800 baud), não está nessa grade, e o UHD arredonda o pedido **sem
erro**. Medido na bancada do demodulador: 0,08% de diferença de taxa derruba
~65% dos pacotes. Então:

- pede-se ao N210 **250 kS/s** (exato, ÷ 400);
- reamostra-se por **24/25**, exato e com estado entre lotes, para publicar
  240 kS/s — ida e volta 240k→250k→240k pelo demodulador real: 60 de 60
  pacotes íntegros, de sinal limpo a SNR 3 dB;
- se a taxa entregue não tiver razão exata com a do cano, o serviço **recusa
  conectar**, com a explicação, em vez de publicar na taxa errada.

## Painel (:8091)

Endereço IP do USRP e argumentos extras do UHD; **Testar conexão**
(`uhd.find`, sem abrir sessão); frequência, taxas, ganho, antena, canal,
referências de relógio e tempo; ligação com a estação (onde publicar, de onde
vem o `tune`); conectar sozinho ao subir. Mostra o que o aparelho **de fato
aceitou** (taxa entregue, sintonia real, ganho, antena), as informações da
placa, o espectro ao vivo, contadores e o registro de eventos.

A configuração é um JSON no volume `usrp_config` (`GRS_IQ_RX_CONFIG`) e
sobrevive a restart. Sem o rádio o serviço fica de pé e diz por que não
conectou — o receptor em C sai com `EXIT_FAILURE`, e aí não haveria onde
corrigir o IP.

## O que foi confirmado, e o que não

Confirmado sem o rádio, contra o `python3-uhd` 4.3.0.0 do Debian bookworm:
cada chamada usada (assinaturas inspecionadas no pacote instalado);
`uhd.find` responde em ~0,5 s e `MultiUSRP` falha em ~0,7 s sem aparelho, sem
travar; os testes de aparelho usam os tipos REAIS do UHD, com só o
`MultiUSRP` substituído.

**Não confirmado — exige o N210:** se o `recv()` entrega amostras, se a
sintonia acontece, a compatibilidade da imagem de FPGA/firmware do rádio com o
UHD 4.3, e o comportamento do UDP atrás do NAT do Docker (a descoberta por
broadcast não atravessa; o teste por IP sim). A checklist do painel diz o que
conferir.

## Testes

```bash
docker build -f ../../docker/grs-iq-rx-usrp.Dockerfile -t grs-iq-rx-usrp:test ..
docker run --rm -v "$PWD/tests:/app/tests" grs-iq-rx-usrp:test \
    sh -c "pip3 install --break-system-packages -q 'pytest>=8,<9'; python3 -m pytest -q tests"
```

56 testes: configuração, reamostrador, serviço (ZMQ de verdade, UHD falso
que imita a grade de taxas do N210), painel (HTTP de verdade) e aparelho
(tipos reais do UHD).
