<img src="https://github.com/user-attachments/assets/cc34964f-76fe-4f6f-a4c8-ed07e8ca9ae1" width="200" alt="libvpn" />

![vibecoded](https://img.shields.io/badge/vibecoded-545454)
[![open-source](https://img.shields.io/badge/open--source-545454)](LICENSE)
[![release](https://img.shields.io/badge/release-tag--v0.0.1-955ca5)](https://github.com/hikariatama/libvpn/releases/tag/v0.0.1)
[![CI](https://github.com/hikariatama/libvpn/actions/workflows/ci.yml/badge.svg)](https://github.com/hikariatama/libvpn/actions/workflows/ci.yml)
[![AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-545454)](LICENSE)

> A native macOS menu-bar client for multi-hop VLESS routing through sing-box. Each route has its own server chain and kill switch.

<img src="https://github.com/user-attachments/assets/808487c4-31b1-4145-996e-a03c9e66c16a" width="476" alt="libvpn screenshot" />

## Download

<table>
  <thead><tr><th>Platform</th><th>Download</th></tr></thead>
  <tbody>
    <tr><td>macOS · Apple Silicon</td><td><a href="https://github.com/hikariatama/libvpn/releases/download/v0.0.1/libvpn-0.0.1-arm64.dmg"><img alt="DMG Apple Silicon" src="https://img.shields.io/badge/DMG-Apple%20Silicon-955ca5" /></a> <a href="https://github.com/hikariatama/libvpn/releases/download/v0.0.1/libvpn-0.0.1-arm64.zip"><img alt="ZIP Apple Silicon" src="https://img.shields.io/badge/ZIP-Apple%20Silicon-545454" /></a></td></tr>
    <tr><td>macOS · Intel</td><td><a href="https://github.com/hikariatama/libvpn/releases/download/v0.0.1/libvpn-0.0.1-x86_64.dmg"><img alt="DMG Intel" src="https://img.shields.io/badge/DMG-Intel-955ca5" /></a> <a href="https://github.com/hikariatama/libvpn/releases/download/v0.0.1/libvpn-0.0.1-x86_64.zip"><img alt="ZIP Intel" src="https://img.shields.io/badge/ZIP-Intel-545454" /></a></td></tr>
  </tbody>
</table>

CI replaces the draft for this version on rebuild. Before publication, maintainers can download it from [Releases](https://github.com/hikariatama/libvpn/releases). Checksums: `SHA256SUMS.txt`.


## Prerequisites

- macOS 14+ to run the app.
- macOS 26 SDK, Command Line Tools and Python 3.10+ to build.

## Checks

```sh
./test.sh
```

## Build

```sh
./build.sh
./test.sh
```

The app, ZIP and DMG are written to `outputs/`. Builds are ad-hoc signed, not notarized.
Installing the system helper requires explicit administrator authorization; builds and tests never install it or connect a VPN.

No servers or routing rules are bundled. Add your own VLESS/TCP/TLS servers and chains in settings.

See [setup, routing and packaging](docs/reference.md), [signed helper updates](docs/updates.md), [security](SECURITY.md) and [third-party licenses](THIRD_PARTY_NOTICES.md).
