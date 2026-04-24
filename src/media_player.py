from __future__ import annotations

import logging
from typing import Any

from ucapi import EntityTypes, StatusCodes, media_player
from ucapi_framework import Entity, create_entity_id

from config import RadioDeviceConfig
from device import FrontierSiliconDevice

_LOG = logging.getLogger("media_player")


class FrontierSiliconMediaPlayer(media_player.MediaPlayer, Entity):
    def __init__(self, device_config: RadioDeviceConfig, device: FrontierSiliconDevice) -> None:
        self._device_config = device_config
        self._device = device
        entity_id = create_entity_id(EntityTypes.MEDIA_PLAYER, device_config.identifier)
        super().__init__(
            entity_id,
            device_config.name,
            features=[
                media_player.Features.ON_OFF,
                media_player.Features.VOLUME,
                media_player.Features.VOLUME_UP_DOWN,
                media_player.Features.MUTE_TOGGLE,
                media_player.Features.SELECT_SOURCE,
                media_player.Features.PLAY_PAUSE,
                media_player.Features.STOP,
                media_player.Features.NEXT,
                media_player.Features.PREVIOUS,
            ],
            attributes={
                media_player.Attributes.STATE: media_player.States.UNKNOWN,
                media_player.Attributes.SOURCE_LIST: [],
            },
            device_class=media_player.DeviceClasses.SPEAKER,
            cmd_handler=self.handle_command,
        )
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        state = self._device.state
        play_status = (state.play_status or "").lower()

        if state.power is False:
            mapped_state = media_player.States.OFF
        elif play_status in {"play", "playing"}:
            mapped_state = media_player.States.PLAYING
        elif play_status in {"pause", "paused"}:
            mapped_state = media_player.States.PAUSED
        elif play_status in {"stop", "stopped"}:
            mapped_state = media_player.States.STOPPED
        elif state.power is True:
            mapped_state = media_player.States.ON
        else:
            mapped_state = media_player.States.UNKNOWN

        attrs: dict[str, Any] = {
            media_player.Attributes.STATE: mapped_state,
            media_player.Attributes.MUTED: state.muted,
            media_player.Attributes.VOLUME: state.volume,
            media_player.Attributes.SOURCE: state.source,
            media_player.Attributes.SOURCE_LIST: state.source_list,
            media_player.Attributes.MEDIA_TITLE: state.media_title,
            media_player.Attributes.MEDIA_ARTIST: state.media_artist,
            media_player.Attributes.MEDIA_ALBUM: state.media_album,
            media_player.Attributes.MEDIA_IMAGE_URL: state.media_image_url,
            media_player.Attributes.MEDIA_TYPE: "radio",
        }
        if state.media_position is not None:
            attrs[media_player.Attributes.MEDIA_POSITION] = state.media_position
        if state.media_position_updated_at is not None:
            attrs[media_player.Attributes.MEDIA_POSITION_UPDATED_AT] = state.media_position_updated_at

        self.update({key: value for key, value in attrs.items() if value is not None})

    async def handle_command(self, _entity: Any, cmd_id: str, params: dict[str, Any] | None = None) -> StatusCodes:
        try:
            if cmd_id == media_player.Commands.ON:
                await self._device.power_on()
                return StatusCodes.OK
            if cmd_id == media_player.Commands.OFF:
                await self._device.power_off()
                return StatusCodes.OK
            if cmd_id == media_player.Commands.TOGGLE:
                await self._device.set_power(not bool(self._device.state.power))
                return StatusCodes.OK
            if cmd_id == media_player.Commands.VOLUME_UP:
                await self._device.volume_up()
                return StatusCodes.OK
            if cmd_id == media_player.Commands.VOLUME_DOWN:
                await self._device.volume_down()
                return StatusCodes.OK
            if cmd_id == media_player.Commands.VOLUME:
                await self._device.set_volume((params or {}).get("volume"))
                return StatusCodes.OK
            if cmd_id == media_player.Commands.MUTE_TOGGLE:
                await self._device.mute_toggle()
                return StatusCodes.OK
            if cmd_id == media_player.Commands.SELECT_SOURCE:
                source = (params or {}).get("source")
                if not source:
                    return StatusCodes.BAD_REQUEST
                await self._device.select_source(source)
                return StatusCodes.OK
            if cmd_id == media_player.Commands.PLAY_PAUSE:
                await self._device.play_pause()
                return StatusCodes.OK
            if cmd_id == media_player.Commands.PLAY:
                await self._device.play()
                return StatusCodes.OK
            if cmd_id == media_player.Commands.PAUSE:
                await self._device.pause()
                return StatusCodes.OK
            if cmd_id == media_player.Commands.STOP:
                await self._device.stop()
                return StatusCodes.OK
            if cmd_id == media_player.Commands.NEXT:
                await self._device.next()
                return StatusCodes.OK
            if cmd_id == media_player.Commands.PREVIOUS:
                await self._device.previous()
                return StatusCodes.OK
        except Exception as exc:
            _LOG.warning("[%s] command %s failed: %s", self._device_config.name, cmd_id, exc)
            return StatusCodes.SERVICE_UNAVAILABLE

        return StatusCodes.NOT_IMPLEMENTED
