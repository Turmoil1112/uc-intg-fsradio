from __future__ import annotations

from typing import Any

import ucapi

import config
from fsradio.client import FrontierSiliconClient, PresetEntry


class FrontierSiliconPresetButton(ucapi.Button):
    def __init__(self, device: config.RadioDevice, client: FrontierSiliconClient, preset: PresetEntry) -> None:
        self._device = device
        self._client = client
        self._preset = preset
        suffix = f"preset_{preset.number or preset.id}"
        entity_id = config.create_entity_id(device.id, ucapi.EntityTypes.BUTTON, suffix=suffix)
        label = preset.name if preset.name else f"Preset {preset.number or preset.id}"
        super().__init__(entity_id, f"{device.name}: {label}", cmd_handler=self.command)

    async def command(
        self,
        _entity: ucapi.Button,
        _cmd_id: str,
        _params: dict[str, Any] | None,
        _websocket: Any,
    ) -> ucapi.StatusCodes:
        await self._client.select_preset_by_id(self._preset.id)
        return ucapi.StatusCodes.OK
