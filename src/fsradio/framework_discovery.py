from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from ucapi_framework.discovery import DiscoveredDevice, SSDPDiscovery


class FrontierSiliconSSDPDiscovery(SSDPDiscovery):
    def __init__(self, timeout: int = 5) -> None:
        super().__init__(
            search_target="ssdp:all",
            timeout=timeout,
            device_filter=self._device_filter,
        )

    @staticmethod
    def _device_filter(raw_device: dict[str, Any]) -> bool:
        server = str(raw_device.get("server", "")).lower()
        st = str(raw_device.get("st", "")).lower()
        usn = str(raw_device.get("usn", "")).lower()
        location = str(raw_device.get("location", "")).lower()

        haystack = " ".join((server, st, usn, location))
        return any(token in haystack for token in ("frontier", "fsapi", "reciva", "undok"))

    def parse_ssdp_device(self, raw_device: dict[str, Any]) -> DiscoveredDevice | None:
        location = str(raw_device.get("location", "")).strip()
        usn = str(raw_device.get("usn", "")).strip()
        server = str(raw_device.get("server", "")).strip()

        if not location:
            return None

        address = self._extract_address_from_location(location)
        if not address:
            return None

        identifier = (
            usn.replace("uuid:", "").replace("::upnp:rootdevice", "").strip()
            or address
        )

        speaker_name = str(raw_device.get("speaker-name", "")).strip()
        friendly_name = self._read_friendly_name(location) if not speaker_name else None
        name = speaker_name or friendly_name or address

        return DiscoveredDevice(
            identifier=identifier,
            name=name,
            address=address,
            extra_data={
                "location": location,
                "usn": usn,
                "server": server,
                "base_url": f"http://{address}:80/device",
            },
        )

    @staticmethod
    def _extract_address_from_location(location: str) -> str | None:
        try:
            without_scheme = location.split("://", 1)[1]
            host_port_path = without_scheme.split("/", 1)[0]
            host = host_port_path.split(":", 1)[0].strip()
            return host or None
        except Exception:
            return None

    @staticmethod
    def _read_friendly_name(location: str) -> str | None:
        try:
            with urllib.request.urlopen(location, timeout=2) as response:
                xml_data = response.read()

            root = ET.fromstring(xml_data)

            # UPnP device descriptions are usually namespaced.
            for elem in root.iter():
                tag = elem.tag.rsplit("}", 1)[-1]
                if tag == "friendlyName":
                    value = (elem.text or "").strip()
                    if value:
                        return value
        except Exception:
            return None

        return None
