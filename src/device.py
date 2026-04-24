from __future__ import annotations

import logging
from dataclasses import replace

from ucapi_framework import PollingDevice

from config import RadioConfigManager, RadioDeviceConfig
from fsradio.client import FrontierSiliconClient, FrontierSiliconState

_LOG = logging.getLogger("device")


class FrontierSiliconDevice(PollingDevice):
    def __init__(
        self,
        device_config: RadioDeviceConfig,
        config_manager: RadioConfigManager | None = None,
        loop=None,
        **kwargs,
    ) -> None:
        super().__init__(device_config, config_manager=config_manager, loop=loop, **kwargs)
        self._client = FrontierSiliconClient(
            device_config.base_url,
            device_config.pin,
            device_config.timeout,
        )
        self._state = FrontierSiliconState(name=device_config.name)
        self._poll_interval = 2.0
    @property
    def identifier(self) -> str:
        return self._device_config.identifier

    @property
    def name(self) -> str:
        return self._device_config.name

    @property
    def address(self) -> str:
        return self._device_config.address

    @property
    def log_id(self) -> str:
        return f"FrontierSiliconDevice[{self.identifier}]"

    async def verify_connection(self) -> bool:
        try:
            await self._client.test_connection()
            return True
        except Exception as exc:
            _LOG.warning("%s verify_connection failed: %s", self.log_id, exc)
            return False

    async def close_connection(self) -> None:
        await self._client.close()

    async def poll_device(self) -> None:
        new_state = await self._client.get_state()
        self._state = new_state
        self.push_update()

        preset_names = [preset.name for preset in new_state.presets if preset.name]
        if self._config_manager is not None and preset_names != list(self._device_config.presets):
            updated = replace(self._device_config, presets=preset_names, name=new_state.name or self._device_config.name)
            self._device_config = updated
            self._config_manager.add_or_update(updated)

    async def power_on(self) -> None:
        await self._client.power_on()

    async def power_off(self) -> None:
        await self._client.power_off()

    async def set_power(self, value: bool) -> None:
        await self._client.set_power(value)

    async def volume_up(self) -> None:
        await self._client.volume_up()

    async def volume_down(self) -> None:
        await self._client.volume_down()

    async def set_volume(self, value: int | float | None) -> None:
        await self._client.set_volume(value)

    async def mute_toggle(self) -> None:
        await self._client.mute_toggle()

    async def select_source(self, source: str) -> None:
        await self._client.select_source(source)

    async def play_pause(self) -> None:
        await self._client.play_pause()

    async def play(self) -> None:
        await self._client.play()

    async def pause(self) -> None:
        await self._client.pause()

    async def stop(self) -> None:
        await self._client.stop()

    async def next(self) -> None:
        await self._client.next()

    async def previous(self) -> None:
        await self._client.previous()

    async def select_preset_by_number(self, number: int) -> None:
        await self._client.select_preset_by_number(number)

    async def establish_connection(self) -> None:
        """
        Framework hook required by the installed ucapi-framework runtime.
    
        For this integration we do not keep a long-lived socket connection.
        We just verify that the radio is reachable and that FSAPI responds.
        """
        ok = await self.verify_connection()
        if not ok:
            raise ConnectionError(f"Unable to connect to Frontier Silicon radio at {self.address}")
