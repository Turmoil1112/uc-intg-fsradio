# Frontier Silicon Radio Integration for Unfolded Circle Remote 3

This project provides a complete Python-based integration scaffold for controlling
Frontier Silicon radios with the Unfolded Circle Remote 3.

## Features

* SSDP-based discovery of Frontier Silicon devices
* JSON-based configuration stored in `UC_CONFIG_HOME`
* `afsapi`-based `FrontierSiliconClient` adapter
* Media player entity with:

  * Power control
  * Volume and mute
  * Playback control (play/pause/stop/next/previous)
  * Source (mode) selection
* Presets exposed as individual button entities
* Centralized metadata mapping with robust fallback logic for:

  * Internet radio
  * DAB
  * FM
  * Bluetooth

## Architecture

The integration is structured into clear layers:

* **`afsapi`** → low-level device communication
* **`FrontierSiliconClient`** → adapter and normalization layer
* **Entities** → UC-facing media player and preset controls

All device-specific quirks and fallback logic are handled inside the client,
keeping the rest of the integration simple and stable.

## Build

Use the official Unfolded Circle build container:

```bash
docker run --rm --name builder \
  --platform=aarch64 \
  --user=$(id -u):$(id -g) \
  -v "$PWD":/workspace \
  docker.io/unfoldedcircle/r2-pyinstaller:3.11.13 \
  bash -c "python -m pip install -r requirements.txt && \
           pyinstaller --clean --onedir --name intg-fsradio src/driver.py"
```

## Configuration

The integration stores its configuration in:

```text
$UC_CONFIG_HOME/fsradio_config.json
```

If no configuration is found, the integration will automatically:

1. Run SSDP discovery
2. Detect available Frontier Silicon radios
3. Add them using a default PIN

You can also manually add devices by modifying the configuration file.

## Metadata Handling

Frontier Silicon devices expose metadata inconsistently depending on the mode.

This integration normalizes metadata using:

* Structured fields (when available)
* Heuristic parsing (e.g. `"Artist - Title"`)
* Mode-specific fallbacks

The resulting fields exposed to the Remote:

* `media_title`
* `media_artist`
* `media_album`
* `channel_name`
* `media_image_url`

## Known Limitations

* Metadata availability depends heavily on the radio model and mode
* FM often provides little to no usable metadata
* Preset identification is best-effort (no reliable API field)

## Development Notes

* Python 3.11+ required
* Designed for PyInstaller builds on ARM (Remote 3)
* Defensive error handling is used throughout the client
* API calls are wrapped to tolerate model differences

## License

This project is provided as a scaffold/example.
Adapt and extend as needed for your own integration.
