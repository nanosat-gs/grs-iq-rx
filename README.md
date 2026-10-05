<h1 align="center">
    GRS IQ RECEIVER
    <br>
</h1>

<h4 align="center">SDR IQ Receiver Application of the SpaceLab's Ground Station.</h4>

<p align="center">
    <a href="https://github.com/spacelab-ufsc/grs-iq-rx">
        <img src="https://img.shields.io/badge/status-development-green?style=for-the-badge">
    </a>
    <a href="https://github.com/spacelab-ufsc/grs-iq-rx/releases">
        <img alt="GitHub commits since latest release (by date)" src="https://img.shields.io/github/commits-since/spacelab-ufsc/grs-iq-rx/latest?style=for-the-badge">
    </a>
    <a href="https://github.com/spacelab-ufsc/grs-iq-rx/blob/main/LICENSE">
        <img src="https://img.shields.io/badge/license-GPL3-yellow?style=for-the-badge">
    </a>
</p>

<p align="center">
    <a href="#overview">Overview</a> •
    <a href="#dependencies">Dependencies</a> •
    <a href="#building">Building</a> •
    <a href="#installing">Installing</a> •
    <a href="#license">License</a>
</p>

## Overview

SDR IQ receiver application of the SpaceLab's ground station. This application reads IQ samples from an SDR (RTL-SDR for now) and transmits it over a Pub/Sub ZMQ socket.

### Branch `station` (nanosat-gs fork)

This is the `nanosat-gs` fork, used by the SpaceLab ground station
([nanosat-gs/grs-station](https://github.com/nanosat-gs/grs-station)). The
`station` branch is based on upstream `dev` (the C/RTL-SDR implementation, the
one that actually receives) and adds:

- **Batched IQ** in the C receiver: one ZMQ message per block, no topic frame,
  `cf32_le`, on `:5556` — the envelope the demodulator and the IQ recorder
  expect. The C receiver tunes once, from `-f`; it does not follow `tune`.
- **USRP N210 receiver** in [`usrp/`](usrp/), in Python on top of
  `python3-uhd`. Same envelope and port as the C receiver; it resamples from a
  rate the N210 can produce exactly (250 kS/s) to the pipeline rate
  (240 kS/s), **follows `tune` on `:5557`** (the `grs-frequency-synthesizer`,
  which applies the Doppler correction), and serves a configuration panel
  (device address, reception, connection test) on `:8091`. The station runs
  one per radio: VHF (panel `:8091`) and UHF (panel `:8092`). See
  [usrp/README.md](usrp/README.md).

## Dependencies

* librtlsdr-dev (>= 2.0.1-2)
* libczmq-dev (>= 4.2.1-2)

### Installation on Ubuntu

```sudo apt install librtlsdr-dev libczmq-dev```

### Installation on Fedora

```sudo dnf install rtl-sdr-devel czmq-devel```

## Building

```make```

Usage: `grs_iq_rx -h` (device index, frequency, gain, sample rate, bandwidth,
PPM error, block size...). The RTL-SDR only accepts 225001–300000 and
900001–3200000 S/s, and outside those ranges the driver silently delivers
another rate; the ground station uses 240000.

## Installing

```make install```

## License

This project is licensed under GPLv3 license.
