from __future__ import annotations

from dataclasses import dataclass, field

from ucapi_framework import BaseConfigManager


@dataclass(slots=True)
class RadioDeviceConfig:
    identifier: str
    name: str
    address: str
    base_url: str
    pin: int
    timeout: float = 2.0
    presets: list[str] = field(default_factory=list)


class RadioConfigManager(BaseConfigManager[RadioDeviceConfig]):
    pass
