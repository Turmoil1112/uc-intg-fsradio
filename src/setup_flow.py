from __future__ import annotations

import logging
from uuid import uuid4

import ucapi
from ucapi import IntegrationSetupError, RequestUserInput, SetupComplete, SetupError
from ucapi_framework import BaseSetupFlow, DiscoveredDevice

from config import RadioConfigManager, RadioDeviceConfig
from fsradio.client import FrontierSiliconClient
from fsradio.framework_discovery import FrontierSiliconSSDPDiscovery

_LOG = logging.getLogger("setup_flow")


class FrontierSiliconSetupFlow(BaseSetupFlow[RadioDeviceConfig]):
    """
    Conservative setup flow for ucapi-framework.

    Flow:
    1. Initial screen: optional manual IP + timeout
    2. If IP was entered: ask for PIN
       Otherwise: run SSDP discovery and ask user to choose a radio
    3. Validate against the radio, persist config, finish setup
    """

    def __init__(self, driver, config_manager: RadioConfigManager) -> None:
        super().__init__(driver=driver, config_manager=config_manager)
        self.driver = driver
        self.config_manager = config_manager
        self._last_timeout: float = 2.0
        self._discovered: dict[str, DiscoveredDevice] = {}
        self._ssdp = FrontierSiliconSSDPDiscovery(timeout=5)

    def _find_existing_by_address(self, address: str) -> RadioDeviceConfig | None:
        for cfg in self.config_manager.all():
            if cfg.address == address:
                return cfg
        return None


    def get_manual_entry_form(self) -> RequestUserInput:
        return RequestUserInput(
            {"en": "Frontier Silicon Radio setup", "de": "Frontier-Silicon-Radio einrichten"},
            [
                {
                    "id": "mode",
                    "label": {"en": "Setup mode", "de": "Setup-Modus"},
                    "field": {
                        "dropdown": {
                            "value": "discover",
                            "items": [
                                {
                                    "id": "discover",
                                    "label": {
                                        "en": "Discover radio automatically",
                                        "de": "Radio automatisch suchen",
                                    },
                                },
                                {
                                    "id": "manual",
                                    "label": {
                                        "en": "Add radio manually",
                                        "de": "Radio manuell hinzufügen",
                                    },
                                },
                                {
                                    "id": "backup",
                                    "label": {
                                        "en": "Create configuration backup",
                                        "de": "Konfigurations-Backup erstellen",
                                    },
                                },
                                {
                                    "id": "restore",
                                    "label": {
                                        "en": "Restore configuration backup",
                                        "de": "Konfigurations-Backup wiederherstellen",
                                    },
                                },
                            ],
                        }
                    },
                },
                {
                    "id": "address",
                    "label": {
                        "en": "IP address (manual setup only)",
                        "de": "IP-Adresse (nur bei manueller Einrichtung)",
                    },
                    "field": {
                        "text": {
                            "value": "",
                        }
                    },
                },
                {
                    "id": "timeout",
                    "label": {
                        "en": "HTTP timeout in seconds",
                        "de": "HTTP-Timeout in Sekunden",
                    },
                    "field": {
                        "number": {
                            "value": 2,
                        }
                    },
                },
            ],
        )

    @staticmethod
    def stable_identifier_from_usn(usn: str, address: str) -> str:
        raw = (usn or "").strip()

        if raw:
            uuid_part = raw.split("::", 1)[0]

            if uuid_part.lower().startswith("uuid:"):
                uuid_part = uuid_part[5:]

            uuid_part = uuid_part.strip().lower()

            if uuid_part:
                return f"fsradio_{uuid_part}"

        return f"fsradio_{address.replace('.', '_').replace(':', '_')}"

    async def query_device(self, input_values: dict) -> RadioDeviceConfig | SetupError:
        """
        Validate a single radio and build the final config object.

        Expected input keys:
        - address
        - pin
        - timeout
        - optional base_url
        """
        address = str(input_values.get("address", "")).strip()
        if not address:
            return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

        try:
            pin = int(str(input_values.get("pin", "1234")).strip())
            timeout = float(input_values.get("timeout", self._last_timeout) or self._last_timeout)
        except Exception:
            return SetupError(error_type=IntegrationSetupError.OTHER)

        base_url = str(input_values.get("base_url") or f"http://{address}:80/device")
        self._last_timeout = timeout

        client = FrontierSiliconClient(base_url, pin, timeout)
        try:
            name = await client.test_connection()
            presets = [preset.name for preset in await client.get_presets() if preset.name]
        except Exception as exc:
            _LOG.warning("Failed to validate radio %s: %s", address, exc)
            return SetupError(error_type=IntegrationSetupError.AUTHORIZATION_ERROR)
        finally:
            await client.close()

        existing = self._find_existing_by_address(address)

        if existing:
            identifier = existing.identifier
        else:
            raw_usn = str(input_values.get("usn") or "").strip()
            identifier = self.stable_identifier_from_usn(raw_usn, address)

        return RadioDeviceConfig(
            identifier=identifier,
            name=name or address,
            address=address,
            base_url=base_url,
            pin=pin,
            timeout=timeout,
            presets=presets,
        )

    async def discover_devices(self, user_input: dict | None = None) -> list[DiscoveredDevice]:
        user_input = user_input or {}
        timeout = float(user_input.get("timeout", 2) or 2)
        self._last_timeout = timeout

        address = str(user_input.get("address", "")).strip()
        if address:
            device = DiscoveredDevice(
                identifier=address,
                name=address,
                address=address,
                extra_data={
                     "base_url": f"http://{address}:80/device",
                },
             )
            self._discovered = {device.identifier: device}
            return [device]

        self._ssdp.timeout = int(max(1, round(timeout)))
        devices = await self._ssdp.discover()
        self._discovered = {device.identifier: device for device in devices}
        return devices

    async def handle_driver_setup(self, msg: ucapi.DriverSetupRequest) -> ucapi.SetupAction:
        """
        Entry point for setup_driver requests from the Remote.
        """
        _LOG.debug("handle_driver_setup reconfigure=%s setup_data=%s", msg.reconfigure, msg.setup_data)

        # Start with our manual/discovery screen.
        # We intentionally do not clear existing config here.
        return self.get_manual_entry_form()

    async def handle_user_data_response(self, msg: ucapi.UserDataResponse) -> ucapi.SetupAction:
        values = msg.input_values or {}
        _LOG.debug("handle_user_data_response input_values=%s", values)

        # Restore submitted
        if "restore_json" in values:
            backup_json = str(values.get("restore_json", "")).strip()
            if not backup_json:
                return SetupError(error_type=IntegrationSetupError.OTHER)

            try:
                self.config_manager.restore_from_backup_json(backup_json)
                if hasattr(self.driver, "register_all_device_instances"):
                    await self.driver.register_all_device_instances(connect=False)
                return SetupComplete()
            except Exception as exc:
                _LOG.warning("Backup restore failed: %s", exc)
                return SetupError(error_type=IntegrationSetupError.OTHER)

        # Final device setup step
        if "step2.pin" in values:
            address = str(values.get("address", "")).strip()
            base_url = None
            usn = None

            choice = str(values.get("step1.choice", "")).strip()
            if choice and choice in self._discovered:
                selected = self._discovered[choice]
                address = selected.address
                base_url = str(selected.extra_data.get("base_url") or f"http://{address}:80/device")
                usn = selected.extra_data.get("usn")

            if not address:
                return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

            config_or_error = await self.query_device(
                {
                    "address": address,
                    "base_url": base_url,
                    "pin": values.get("step2.pin"),
                    "timeout": values.get("step2.timeout", values.get("timeout", self._last_timeout)),
                    "usn": usn,
                }
            )

            if isinstance(config_or_error, SetupError):
                return config_or_error

            self.config_manager.add_or_update(config_or_error)

            if hasattr(self.driver, "register_all_device_instances"):
                await self.driver.register_all_device_instances(connect=False)

            return SetupComplete()

        mode = str(values.get("mode", "discover")).strip()

        # Backup screen
        if mode == "backup":
            try:
                backup_json = self.config_manager.get_backup_json()
            except Exception as exc:
                _LOG.warning("Backup creation failed: %s", exc)
                return SetupError(error_type=IntegrationSetupError.OTHER)

            return RequestUserInput(
                {"en": "Configuration backup", "de": "Konfigurations-Backup"},
                [
                    {
                        "id": "backup_json",
                        "label": {"en": "Backup JSON", "de": "Backup-JSON"},
                        "field": {
                            "textarea": {
                                "value": backup_json,
                            }
                        },
                    }
                ],
            )

        # Restore input screen
        if mode == "restore":
            return RequestUserInput(
                {"en": "Restore backup", "de": "Backup wiederherstellen"},
                [
                    {
                        "id": "restore_json",
                        "label": {"en": "Backup JSON", "de": "Backup-JSON"},
                        "field": {
                            "textarea": {
                                "value": "",
                            }
                        },
                    }
                ],
            )

        timeout = float(values.get("timeout", self._last_timeout) or self._last_timeout)
        self._last_timeout = timeout

        # Manual setup path
        if mode == "manual":
            address = str(values.get("address", "")).strip()
            if not address:
                return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

            return RequestUserInput(
                {"en": "Enter PIN", "de": "PIN eingeben"},
                [
                    {
                        "id": "info",
                        "label": {"en": "Selected device", "de": "Gewähltes Gerät"},
                        "field": {"label": {"value": {"en": address, "de": address}}},
                    },
                    {
                        "id": "step2.pin",
                        "label": {"en": "PIN", "de": "PIN"},
                        "field": {"text": {"value": "1234"}},
                    },
                    {
                        "id": "step2.timeout",
                        "label": {"en": "HTTP timeout in seconds", "de": "HTTP-Timeout in Sekunden"},
                        "field": {"number": {"value": timeout}},
                    },
                ],
            )

        # Discovery setup path
        devices = await self.discover_devices(values)
        if not devices:
            return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

        dropdown_items = [
            {
                "id": device.identifier,
                "label": {
                    "en": f"{device.name} ({device.address})",
                    "de": f"{device.name} ({device.address})",
                },
            }
            for device in devices
        ]

        return RequestUserInput(
            {"en": "Select radio", "de": "Radio auswählen"},
            [
                {
                    "id": "step1.choice",
                    "label": {"en": "Discovered radios", "de": "Gefundene Radios"},
                    "field": {
                        "dropdown": {
                            "value": "",
                            "items": dropdown_items,
                        }
                    },
                },
                {
                    "id": "step2.pin",
                    "label": {"en": "PIN", "de": "PIN"},
                    "field": {"text": {"value": "1234"}},
                },
                {
                    "id": "step2.timeout",
                    "label": {"en": "HTTP timeout in seconds", "de": "HTTP-Timeout in Sekunden"},
                    "field": {"number": {"value": timeout}},
                },
            ],
        )

    def is_duplicate(self, config: RadioDeviceConfig) -> bool:
        for item in self.config_manager.all():
            if item.address == config.address and item.identifier != config.identifier:
                return True
        return False

