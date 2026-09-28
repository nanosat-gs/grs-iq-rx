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

### USRP (branch `station`)

The [`usrp/`](usrp/) folder holds a second receiver, in Python on top of
`python3-uhd`, for the USRP N210 used by the nanosat-gs station. It
publishes the same envelope as the C receiver (one batch per message, no
topic frame, `cf32_le`, on `:5556`), resamples from a rate the N210 can
produce exactly (250 kS/s) to the pipeline rate (240 kS/s), follows `tune`
on `:5557`, and serves a configuration panel (device address, reception,
connection test) on `:8091`. See [usrp/README.md](usrp/README.md).

## Dependencies

* librtlsdr-dev (>= 2.0.1-2)
* libczmq-dev (>= 4.2.1-2)

### Installation on Ubuntu

```sudo apt install librtlsdr-dev libczmq-dev```

### Installation on Fedora

```sudo dnf install rtl-sdr-devel czmq-devel```

## Building

```make```

## Installing

```make install```

## License

This project is licensed under GPLv3 license.
