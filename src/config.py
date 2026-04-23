from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

_LOG = logging.getLogger("config")


@dataclass(slots=True)
class RadioDevice:
    id: str
    name: str
    address: str
    base_url: str
    pin: int
    timeout: float = 2.0


class Devices:
    def __init__(
        self,
        config_dir: str,
        on_add: Callable[[RadioDevice], None] | None = None,
        on_remove: Callable[[RadioDevice | None], None] | None = None,
    ) -> None:
        self._dir = Path(config_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "devices.json"
        self._on_add = on_add
        self._on_remove = on_remove
        self._devices: dict[str, RadioDevice] = {}
        self.load()

    def load(self) -> None:
        if not self._path.exists():
            self._devices = {}
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._devices = {item["id"]: RadioDevice(**item) for item in raw}

    def store(self) -> None:
        payload = [asdict(item) for item in self._devices.values()]
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def all(self) -> list[RadioDevice]:
        return list(self._devices.values())

    def clear(self) -> None:
        self._devices = {}
        self.store()
        if self._on_remove:
            self._on_remove(None)

    def add(self, device: RadioDevice) -> RadioDevice:
        self._devices[device.id] = device
        self.store()
        if self._on_add:
            self._on_add(device)
        return device

    def remove(self, device_id: str) -> bool:
        device = self._devices.pop(device_id, None)
        self.store()
        if device and self._on_remove:
            self._on_remove(device)
        return device is not None

    def get(self, device_id: str) -> RadioDevice | None:
        return self._devices.get(device_id)

    def get_by_address(self, address: str) -> RadioDevice | None:
        for device in self._devices.values():
            if device.address == address:
                return device
        return None


def create_device(name: str, address: str, base_url: str, pin: int, timeout: float = 2.0) -> RadioDevice:
    return RadioDevice(
        id=f"fsradio_{uuid4().hex[:8]}",
        name=name,
        address=address,
        base_url=base_url,
        pin=int(pin),
        timeout=timeout,
    )


def create_entity_id(device_id: str, entity_type: str = "media_player") -> str:
    if entity_type == "media_player":
        return device_id
    return f"{entity_type}.{device_id}"


def device_from_entity_id(entity_id: str) -> str:
    return entity_id.split(".", 1)[-1] if "." in entity_id else entity_id


devices: Devices
