from __future__ import annotations

import logging
from typing import Any

from ucapi import EntityTypes, MediaPlayer, StatusCodes
from ucapi.media_player import Attributes, Commands, DeviceClasses, Features

import config
from fsradio.client import FrontierSiliconClient, FrontierSiliconState

_LOG = logging.getLogger("media_player")


class FrontierSiliconMediaPlayer(MediaPlayer):
    def __init__(self, device_config: config.RadioDevice, client: FrontierSiliconClient) -> None:
        self._device_config = device_config
        self._client = client
        entity_id = config.create_entity_id(device_config.id, EntityTypes.MEDIA_PLAYER)
        super().__init__(
            entity_id,
            device_config.name,
            [
                Features.ON_OFF,
                Features.VOLUME,
                Features.VOLUME_UP_DOWN,
                Features.MUTE_TOGGLE,
                Features.SELECT_SOURCE,
                Features.PLAY_PAUSE,
                Features.STOP,
                Features.NEXT,
                Features.PREVIOUS,
            ],
            {
                Attributes.STATE: "unknown",
                Attributes.SOURCE_LIST: [],
            },
            device_class=DeviceClasses.SPEAKER,
        )

    async def command(self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any) -> StatusCodes:
        try:
            if cmd_id == Commands.ON:
                await self._client.power_on()
                return StatusCodes.OK
            if cmd_id == Commands.OFF:
                await self._client.power_off()
                return StatusCodes.OK
            if cmd_id == Commands.TOGGLE:
                state = await self._client.get_state()
                await self._client.set_power(not bool(state.power))
                return StatusCodes.OK
            if cmd_id == Commands.VOLUME_UP:
                await self._client.volume_up()
                return StatusCodes.OK
            if cmd_id == Commands.VOLUME_DOWN:
                await self._client.volume_down()
                return StatusCodes.OK
            if cmd_id == Commands.VOLUME:
                await self._client.set_volume((params or {}).get("volume"))
                return StatusCodes.OK
            if cmd_id == Commands.MUTE_TOGGLE:
                await self._client.mute_toggle()
                return StatusCodes.OK
            if cmd_id == Commands.SELECT_SOURCE:
                source = (params or {}).get("source")
                if not source:
                    return StatusCodes.BAD_REQUEST
                await self._client.select_source(source)
                return StatusCodes.OK
            if cmd_id == Commands.PLAY_PAUSE:
                await self._client.play_pause()
                return StatusCodes.OK
            if cmd_id == Commands.PLAY:
                await self._client.play()
                return StatusCodes.OK
            if cmd_id == Commands.PAUSE:
                await self._client.pause()
                return StatusCodes.OK
            if cmd_id == Commands.STOP:
                await self._client.stop()
                return StatusCodes.OK
            if cmd_id == Commands.NEXT:
                await self._client.next()
                return StatusCodes.OK
            if cmd_id == Commands.PREVIOUS:
                await self._client.previous()
                return StatusCodes.OK
        except Exception as exc:
            _LOG.warning("[%s] command %s failed: %s", self._device_config.name, cmd_id, exc)
            return StatusCodes.SERVICE_UNAVAILABLE

        _LOG.warning("Unsupported command for %s: %s", self._device_config.name, cmd_id)
        return StatusCodes.NOT_IMPLEMENTED

    def attributes_from_state(self, state: FrontierSiliconState) -> dict[str, Any]:
        play_status = (state.play_status or "").lower()
        if state.power is False:
            mapped_state = "off"
        elif play_status in {"play", "playing"}:
            mapped_state = "playing"
        elif play_status in {"pause", "paused"}:
            mapped_state = "paused"
        elif play_status in {"stop", "stopped"}:
            mapped_state = "stopped"
        elif state.power is True:
            mapped_state = "on"
        else:
            mapped_state = "unknown"

        attrs: dict[str, Any] = {
            Attributes.STATE: mapped_state,
            Attributes.MUTED: state.muted,
            Attributes.VOLUME: state.volume,
            Attributes.SOURCE: state.source,
            Attributes.SOURCE_LIST: state.source_list,
            Attributes.MEDIA_TITLE: state.media_title,
            Attributes.MEDIA_ARTIST: state.media_artist,
            Attributes.MEDIA_ALBUM: state.media_album,
            Attributes.MEDIA_IMAGE_URL: state.media_image_url,
            Attributes.MEDIA_TYPE: "radio",
        }
        if state.media_position is not None:
            attrs[Attributes.MEDIA_POSITION] = state.media_position
        if state.media_position_updated_at is not None:
            attrs[Attributes.MEDIA_POSITION_UPDATED_AT] = state.media_position_updated_at
        return {key: value for key, value in attrs.items() if value is not None}
