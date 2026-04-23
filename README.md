# Frontier Silicon Radio integration scaffold for Unfolded Circle Remote 3

Dieses Projekt ist ein lauffähiges Grundgerüst für eine externe Unfolded-Circle-Integration auf Basis von:

- `ucapi`
- `afsapi`
- SSDP-Discovery für Frontier-Silicon-Radios

## Enthalten

- `FrontierSiliconClient` Adapter um die asynchrone `afsapi`-Library
- SSDP-Discovery
- JSON-basierte Konfiguration im `UC_CONFIG_HOME`
- Setup-Flow mit Discovery, manueller IP und PIN-Eingabe
- `media-player`-Entity mit Basis-Mediensteuerung
- Polling-Loop für Status-Updates

## Start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
UC_CONFIG_HOME=./config UC_DATA_HOME=./data python3 src/driver.py
```

## Build

```bash
docker run --rm --name builder   --platform=aarch64   --user=$(id -u):$(id -g)   -v "$PWD":/workspace   docker.io/unfoldedcircle/r2-pyinstaller:3.11.13   bash -c "python -m pip install -r requirements.txt &&            pyinstaller --clean --onedir --name intg-fsradio src/driver.py"
```

## Hinweise

- Das Gerüst ist bewusst konservativ und robust gehalten.
- `afsapi` wird asynchron über `AFSAPI.create(...)` initialisiert und über einen eigenen Adapter gekapselt.
- Einige FSAPI-Funktionen unterscheiden sich je nach Radio-Modell. Deshalb kapselt `FrontierSiliconClient` alle direkten Bibliothekszugriffe an einer Stelle.
- Presets, Browse, Favoriten, Sleep, Alarm und tieferes Quellen-Mapping sind als nächster Ausbau gedacht.
