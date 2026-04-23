# Frontier Silicon Radio integration scaffold for Unfolded Circle Remote 3

This project provides a working scaffold for an external Unfolded Circle integration based on:

* `ucapi`
* `afsapi`
* SSDP discovery for Frontier Silicon radios

## Included

* `FrontierSiliconClient` adapter wrapping the asynchronous `afsapi` library
* SSDP discovery
* JSON-based configuration in `UC_CONFIG_HOME`
* Setup flow with discovery, manual IP entry, and PIN input
* `media-player` entity with basic media controls
* Polling loop for status updates

## Start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
UC_CONFIG_HOME=./config UC_DATA_HOME=./data python3 src/driver.py
```

## Build

```bash
docker run --rm --name builder \
  --platform=aarch64 \
  --user=$(id -u):$(id -g) \
  -v "$PWD":/workspace \
  docker.io/unfoldedcircle/r2-pyinstaller:3.11.13 \
  bash -c "python -m pip install -r requirements.txt && \
           pyinstaller --clean --onedir --name intg-fsradio src/driver.py"
```

## Notes

* The scaffold is intentionally conservative and robust.
* `afsapi` is initialized asynchronously via `AFSAPI.create(...)` and encapsulated in a dedicated adapter.
* Some FSAPI functions vary depending on the radio model. Therefore, `FrontierSiliconClient` centralizes all direct library interactions.
* Presets, browsing, favorites, sleep, alarm, and more advanced source mapping are planned as future enhancements.
