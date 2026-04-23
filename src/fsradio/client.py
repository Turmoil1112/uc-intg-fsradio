from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from afsapi import AFSAPI

_LOG = logging.getLogger("fsradio.client")


class FrontierSiliconError(Exception):
    """Raised when communication with the radio fails."""


@dataclass(slots=True)
class FrontierSiliconState:
    name: str = ""
    power: bool | None = None
    muted: bool | None = None
    volume: int | None = None
    volume_steps: int | None = None
    source: str | None = None
    source_list: list[str] = field(default_factory=list)
    media_title: str | None = None
    media_artist: str | None = None
    media_album: str | None = None
    media_image_url: str | None = None
    play_status: str | None = None
    media_position: float | None = None
    media_position_updated_at: str | None = None


class FrontierSiliconClient:
    def __init__(self, base_url: str, pin: int | str, timeout: float = 2.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._pin = int(pin)
        self._timeout = int(timeout)
        self._api: AFSAPI | None = None
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return self._base_url

    async def close(self) -> None:
        async with self._lock:
            if self._api is not None:
                try:
                    await self._api.close()
                finally:
                    self._api = None

    async def test_connection(self) -> str:
        state = await self.get_state()
        return state.name or "Frontier Silicon radio"

    async def get_state(self) -> FrontierSiliconState:
        async with self._lock:
            api = await self._ensure_api()
            try:
                modes = await api.get_modes() or []
                current_mode = await api.get_mode()
                play_status = await api.get_play_status()
                media_position = await api.get_play_position()
                return FrontierSiliconState(
                    name=str(await api.get_friendly_name() or ""),
                    power=_to_bool(await api.get_power()),
                    muted=_to_bool(await api.get_mute()),
                    volume=_safe_int(await api.get_volume()),
                    volume_steps=_safe_int(await api.get_volume_steps()),
                    source=_mode_to_name(current_mode),
                    source_list=[_mode_to_name(item) for item in modes if item is not None],
                    media_title=_none_if_empty(await api.get_play_name()),
                    media_artist=_none_if_empty(await api.get_play_artist()),
                    media_album=_none_if_empty(await api.get_play_album()),
                    media_image_url=_none_if_empty(await api.get_play_graphic()),
                    play_status=_none_if_empty(_enum_name(play_status)),
                    media_position=_safe_float(media_position),
                    media_position_updated_at=datetime.now(tz=UTC).isoformat(),
                )
            except Exception as exc:  # pragma: no cover - transport/lib dependent
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc

    async def power_on(self) -> None:
        await self.set_power(True)

    async def power_off(self) -> None:
        await self.set_power(False)

    async def set_power(self, value: bool) -> None:
        await self._call_bool_setter('set_power', bool(value))

    async def volume_up(self) -> None:
        state = await self.get_state()
        if state.volume is None:
            return
        step = max(1, int((state.volume_steps or 20) / 20))
        await self.set_volume(state.volume + step)

    async def volume_down(self) -> None:
        state = await self.get_state()
        if state.volume is None:
            return
        step = max(1, int((state.volume_steps or 20) / 20))
        await self.set_volume(state.volume - step)

    async def set_volume(self, value: int | float | None) -> None:
        if value is None:
            return
        state = await self.get_state()
        upper = state.volume_steps or 20
        clamped = max(0, min(int(value), upper))
        await self._call_bool_setter('set_volume', clamped)

    async def mute_toggle(self) -> None:
        state = await self.get_state()
        await self.set_mute(not bool(state.muted))

    async def set_mute(self, value: bool) -> None:
        await self._call_bool_setter('set_mute', bool(value))

    async def select_source(self, source: str) -> None:
        async with self._lock:
            api = await self._ensure_api()
            try:
                modes = await api.get_modes() or []
                selected: Any = None
                for item in modes:
                    if _mode_to_name(item).lower() == source.lower():
                        selected = item
                        break
                if selected is None:
                    raise FrontierSiliconError(f'Unknown source: {source}')
                result = await api.set_mode(selected)
                if result is False:
                    raise FrontierSiliconError(f'Failed to select source: {source}')
            except Exception as exc:  # pragma: no cover - transport/lib dependent
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc

    async def play_pause(self) -> None:
        state = await self.get_state()
        play_status = (state.play_status or '').lower()
        if play_status in {'play', 'playing', 'buffering', 'loading', 'rebuffering'}:
            await self.pause()
        else:
            await self.play()

    async def play(self) -> None:
        await self._call_transport('play')

    async def pause(self) -> None:
        await self._call_transport('pause')

    async def stop(self) -> None:
        await self._call_transport('stop')

    async def next(self) -> None:
        await self._call_transport('forward')

    async def previous(self) -> None:
        await self._call_transport('rewind')

    async def _ensure_api(self) -> AFSAPI:
        if self._api is None:
            self._api = await AFSAPI.create(self._base_url, self._pin, self._timeout)
        return self._api

    async def _invalidate_api(self) -> None:
        if self._api is not None:
            try:
                await self._api.close()
            except Exception:
                pass
            finally:
                self._api = None

    async def _call_bool_setter(self, method_name: str, value: Any) -> None:
        async with self._lock:
            api = await self._ensure_api()
            try:
                method = getattr(api, method_name)
                result = await method(value)
                if result is False:
                    raise FrontierSiliconError(f'{method_name} failed')
            except Exception as exc:  # pragma: no cover - transport/lib dependent
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc

    async def _call_transport(self, method_name: str) -> None:
        async with self._lock:
            api = await self._ensure_api()
            try:
                method = getattr(api, method_name)
                result = await method()
                if result is False:
                    raise FrontierSiliconError(f'{method_name} failed')
            except Exception as exc:  # pragma: no cover - transport/lib dependent
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {'1', 'true', 'yes', 'on'}:
        return True
    if text in {'0', 'false', 'no', 'off'}:
        return False
    return None


def _none_if_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, 'name', None) or str(value)


def _mode_to_name(mode: Any) -> str:
    if mode is None:
        return ''
    for attr in ('label', 'name', 'value', 'id'):
        if hasattr(mode, attr):
            try:
                raw = getattr(mode, attr)
                if raw is not None:
                    text = str(raw).strip()
                    if text:
                        return text
            except Exception:
                pass
    return str(mode)
