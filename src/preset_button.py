from __future__ import annotations

from typing import Any

import ucapi
from ucapi import EntityTypes
from ucapi_framework import Entity, create_entity_id

from config import RadioDeviceConfig
from device import FrontierSiliconDevice


class FrontierSiliconPresetButton(ucapi.Button, Entity):
    def __init__(self, device_config: RadioDeviceConfig, device: FrontierSiliconDevice, preset_name: str, preset_number: int) -> None:
        self._device_config = device_config
        self._device = device
        self._preset_name = preset_name
        self._preset_number = preset_number
        entity_id = create_entity_id(
            EntityTypes.BUTTON,
            device_config.identifier,
            f"preset_{preset_number}",
        )
        super().__init__(
            entity_id,
            f"{device_config.name}: {preset_name or f'Preset {preset_number}'}",
            cmd_handler=self.handle_command,
        )

    async def handle_command(
        self,
        _entity: ucapi.Button,
        _cmd_id: str,
        _params: dict[str, Any] | None,
        _websocket: Any | None = None,
    ) -> ucapi.StatusCodes:
        await self._device.select_preset_by_number(self._preset_number)
        return ucapi.StatusCodes.OK
