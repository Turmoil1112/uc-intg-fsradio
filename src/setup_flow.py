from __future__ import annotations

from uuid import uuid4

from ucapi import IntegrationSetupError, RequestUserInput, SetupError
from ucapi_framework import BaseSetupFlow

from config import RadioDeviceConfig
from fsradio.client import FrontierSiliconClient


class FrontierSiliconSetupFlow(BaseSetupFlow[RadioDeviceConfig]):
    def get_manual_entry_form(self) -> RequestUserInput:
        return RequestUserInput(
            {"en": "Add Frontier Silicon Radio", "de": "Frontier-Silicon-Radio hinzufügen"},
            [
                {
                    "id": "address",
                    "label": {"en": "IP address", "de": "IP-Adresse"},
                    "field": {"text": {"value": ""}},
                },
                {
                    "id": "pin",
                    "label": {"en": "PIN", "de": "PIN"},
                    "field": {"text": {"value": "1234"}},
                },
                {
                    "id": "timeout",
                    "label": {"en": "HTTP timeout in seconds", "de": "HTTP-Timeout in Sekunden"},
                    "field": {"number": {"value": 2}},
                },
            ],
        )

    def get_additional_discovery_fields(self) -> list[dict]:
        return [
            {
                "id": "pin",
                "label": {"en": "PIN", "de": "PIN"},
                "field": {"text": {"value": "1234"}},
            },
            {
                "id": "timeout",
                "label": {"en": "HTTP timeout in seconds", "de": "HTTP-Timeout in Sekunden"},
                "field": {"number": {"value": 2}},
            },
        ]

    async def prepare_input_from_discovery(self, discovered, additional_input: dict) -> dict:
        return {
            "identifier": self.stable_identifier_from_usn(
                discovered.extra_data.get("usn", ""),
                discovered.address,
            ),
            "name": discovered.name,
            "address": discovered.address,
            "base_url": discovered.extra_data.get("base_url") or f"http://{discovered.address}:80/device",
            "usn": discovered.extra_data.get("usn"),
            "pin": additional_input.get("pin", "1234"),
            "timeout": additional_input.get("timeout", 2),
        }

    async def query_device(self, input_values: dict) -> RadioDeviceConfig | SetupError:
        address = str(input_values.get("address", "")).strip()
        if not address:
            return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

        try:
            pin = int(str(input_values.get("pin", "1234")).strip())
            timeout = float(input_values.get("timeout", 2) or 2)
        except Exception:
            return SetupError(error_type=IntegrationSetupError.OTHER)

        base_url = str(input_values.get("base_url") or f"http://{address}:80/device")

        identifier = str(input_values.get("identifier") or "").strip()
        if not identifier:
            identifier = self.stable_identifier_from_usn(
                str(input_values.get("usn") or ""),
                address,
            )

        client = FrontierSiliconClient(base_url, pin, timeout)
        try:
            name = await client.test_connection()
            presets = [preset.name for preset in await client.get_presets() if preset.name]
        except Exception:
            return SetupError(error_type=IntegrationSetupError.CONNECTION_REFUSED)
        finally:
            await client.close()

        return RadioDeviceConfig(
            identifier=identifier,
            name=name or input_values.get("name") or address,
            address=address,
            base_url=base_url,
            pin=pin,
            timeout=timeout,
            presets=presets,
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