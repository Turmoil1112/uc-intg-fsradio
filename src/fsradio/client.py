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
class PresetEntry:
    id: str
    name: str
    number: int | None = None


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
    presets: list[PresetEntry] = field(default_factory=list)
    active_preset_id: str | None = None


class FrontierSiliconClient:
    def __init__(self, base_url: str, pin: int | str, timeout: float = 2.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._pin = int(pin)
        self._timeout = int(timeout)
        self._api: AFSAPI | None = None
        self._lock = asyncio.Lock()
        self._last_selected_preset_id: str | None = None

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
                presets = await self._fetch_presets(api)

                source = _mode_to_name(current_mode)
                metadata = await self._read_metadata(api, source)

                active_preset_id = self._guess_active_preset_id(
                    presets,
                    title=metadata["media_title"],
                    artist=metadata["media_artist"],
                    channel=metadata["channel_name"],
                )

                return FrontierSiliconState(
                    name=str(await api.get_friendly_name() or ""),
                    power=_to_bool(await api.get_power()),
                    muted=_to_bool(await api.get_mute()),
                    volume=_safe_int(await api.get_volume()),
                    volume_steps=_safe_int(await api.get_volume_steps()),
                    source=source,
                    source_list=[_mode_to_name(item) for item in modes if item is not None],
                    media_title=metadata["media_title"],
                    media_artist=metadata["media_artist"],
                    media_album=metadata["media_album"],
                    media_image_url=metadata["media_image_url"],
                    play_status=_none_if_empty(_enum_name(play_status)),
                    media_position=_safe_float(media_position),
                    media_position_updated_at=datetime.now(tz=UTC).isoformat(),
                    presets=presets,
                    active_preset_id=active_preset_id,
                )
            except Exception as exc:
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc

    async def get_presets(self) -> list[PresetEntry]:
        async with self._lock:
            api = await self._ensure_api()
            try:
                return await self._fetch_presets(api)
            except Exception as exc:
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc

    async def power_on(self) -> None:
        await self.set_power(True)

    async def power_off(self) -> None:
        await self.set_power(False)

    async def set_power(self, value: bool) -> None:
        await self._call_setter("set_power", bool(value))

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
        await self._call_setter("set_volume", clamped)

    async def mute_toggle(self) -> None:
        state = await self.get_state()
        await self.set_mute(not bool(state.muted))

    async def set_mute(self, value: bool) -> None:
        await self._call_setter("set_mute", bool(value))

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
                    raise FrontierSiliconError(f"Unknown source: {source}")
                result = await api.set_mode(selected)
                if result is False:
                    raise FrontierSiliconError(f"Failed to select source: {source}")
            except Exception as exc:
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc

    async def play_pause(self) -> None:
        state = await self.get_state()
        play_status = (state.play_status or "").lower()
        if play_status in {"play", "playing", "buffering", "loading", "rebuffering"}:
            await self.pause()
        else:
            await self.play()

    async def play(self) -> None:
        await self._call_transport("play")

    async def pause(self) -> None:
        await self._call_transport("pause")

    async def stop(self) -> None:
        await self._call_transport("stop")

    async def next(self) -> None:
        await self._call_transport("forward")

    async def previous(self) -> None:
        await self._call_transport("rewind")

    async def select_preset_by_id(self, preset_id: str) -> None:
        async with self._lock:
            api = await self._ensure_api()
            try:
                result = await api.select_preset(preset_id)
                if result is False:
                    raise FrontierSiliconError(f"Failed to select preset: {preset_id}")
                self._last_selected_preset_id = str(preset_id)
            except Exception as exc:
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc

    async def select_preset_by_number(self, number: int) -> None:
        presets = await self.get_presets()
        for preset in presets:
            if preset.number == number:
                await self.select_preset_by_id(preset.id)
                return
        raise FrontierSiliconError(f"Unknown preset number: {number}")

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

    async def _call_setter(self, method_name: str, value: Any) -> None:
        async with self._lock:
            api = await self._ensure_api()
            try:
                method = getattr(api, method_name)
                result = await method(value)
                if result is False:
                    raise FrontierSiliconError(f"{method_name} failed")
            except Exception as exc:
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc

    async def _call_transport(self, method_name: str) -> None:
        async with self._lock:
            api = await self._ensure_api()
            try:
                method = getattr(api, method_name)
                result = await method()
                if result is False:
                    raise FrontierSiliconError(f"{method_name} failed")
            except Exception as exc:
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc

    async def _read_metadata(self, api: AFSAPI, source: str | None) -> dict[str, str | None]:
        play_name = _normalize_metadata(await _safe_api_call(api, "get_play_name"))
        if play_name is None:
            play_name = _normalize_metadata(await _safe_api_call(api, "get_play_info_name"))

        play_text = _normalize_metadata(await _safe_api_call(api, "get_play_text"))
        if play_text is None:
            play_text = _normalize_metadata(await _safe_api_call(api, "get_play_info_text"))

        play_artist = _normalize_metadata(await _safe_api_call(api, "get_play_artist"))
        if play_artist is None:
            play_artist = _normalize_metadata(await _safe_api_call(api, "get_play_info_artist"))

        play_album = _normalize_metadata(await _safe_api_call(api, "get_play_album"))
        if play_album is None:
            play_album = _normalize_metadata(await _safe_api_call(api, "get_play_info_album"))

        play_image = _normalize_metadata(await _safe_api_call(api, "get_play_graphic"))
        if play_image is None:
            play_image = _normalize_metadata(await _safe_api_call(api, "get_play_info_image"))

        source_cf = (source or "").strip().casefold()
        channel_name: str | None = None
        media_title: str | None = None
        media_artist: str | None = None

        if source_cf in {"internet radio", "netradio", "radio", "ir", "network"}:
            channel_name = play_name

            if play_artist:
                media_artist = play_artist
                media_title = play_text or play_name
            else:
                split_artist, split_title = _split_artist_title(play_text)
                media_artist = split_artist
                media_title = split_title

            if not media_title and play_name:
                media_title = play_name

        elif source_cf == "dab":
            channel_name = play_name

            if play_artist:
                media_artist = play_artist
                media_title = play_text
            else:
                split_artist, split_title = _split_artist_title(play_text)
                media_artist = split_artist
                media_title = split_title or play_text

            if not media_title and play_name:
                media_title = play_name

        elif source_cf in {"fm", "ukw"}:
            channel_name = play_name
            split_artist, split_title = _split_artist_title(play_text)
            media_artist = play_artist or split_artist
            media_title = split_title or play_text or play_name

        else:
            split_artist, split_title = _split_artist_title(play_text)
            if source_cf in {"bluetooth", "bt"}:
                channel_name = play_name
            media_artist = play_artist or split_artist
            media_title = split_title or play_text or play_name

        if not channel_name and self._last_selected_preset_id and source_cf in {"internet radio", "dab", "fm"}:
            for preset in await self._fetch_presets(api):
                if preset.id == self._last_selected_preset_id:
                    channel_name = preset.name
                    break

        if channel_name and media_title and channel_name.casefold() == media_title.casefold() and media_artist:
            # keep channel and title equal only when artist exists; otherwise prefer useful title fallback
            pass

        _LOG.debug(
            "metadata source=%s name=%r text=%r artist=%r album=%r image=%r -> channel=%r title=%r artist=%r",
            source,
            play_name,
            play_text,
            play_artist,
            play_album,
            play_image,
            channel_name,
            media_title,
            media_artist,
        )

        return {
            "channel_name": channel_name,
            "media_title": media_title,
            "media_artist": media_artist,
            "media_album": play_album,
            "media_image_url": play_image,
        }

    async def _fetch_presets(self, api: AFSAPI) -> list[PresetEntry]:
        raw = await api.get_presets() or []
        presets: list[PresetEntry] = []
        for idx, item in enumerate(raw, start=1):
            preset_id = _extract(item, "id", "preset_id", "key", fallback=str(idx))
            name = _extract(item, "name", "label", "title", fallback=f"Preset {idx}")
            number = _safe_int(_extract(item, "number", "index", fallback=idx))
            presets.append(PresetEntry(id=str(preset_id), name=str(name), number=number))
        return presets

    def _guess_active_preset_id(
        self,
        presets: list[PresetEntry],
        *,
        title: str | None,
        artist: str | None,
        channel: str | None = None,
    ) -> str | None:
        haystack = " ".join(part.lower() for part in (title or "", artist or "", channel or "") if part)
        if haystack:
            for preset in presets:
                if preset.name and preset.name.lower() in haystack:
                    self._last_selected_preset_id = preset.id
                    return preset.id
        return self._last_selected_preset_id


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
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
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
    return getattr(value, "name", None) or str(value)


def _mode_to_name(mode: Any) -> str:
    if mode is None:
        return ""
    for attr in ("label", "name", "value", "id"):
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


def _extract(item: Any, *attrs: str, fallback: Any = None) -> Any:
    if isinstance(item, dict):
        for attr in attrs:
            if attr in item and item[attr] not in (None, ""):
                return item[attr]
        return fallback
    for attr in attrs:
        if hasattr(item, attr):
            value = getattr(item, attr)
            if value not in (None, ""):
                return value
    return fallback


async def _safe_api_call(api: AFSAPI, method_name: str) -> Any:
    method = getattr(api, method_name, None)
    if method is None:
        return None
    try:
        return await method()
    except Exception:
        return None


def _normalize_metadata(value: Any) -> str | None:
    text = _none_if_empty(value)
    if text is None:
        return None
    if text.casefold() in {
        "n/a",
        "na",
        "unknown",
        "unknown artist",
        "unknown title",
        "unknown album",
        "---",
        "-",
        ".",
    }:
        return None
    return text


def _split_artist_title(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None

    for sep in (" - ", " – ", " — ", " | ", " ~ ", " / "):
        if sep in text:
            left, right = text.split(sep, 1)
            left = left.strip() or None
            right = right.strip() or None
            if left and right:
                return left, right

    return None, text.strip() or None
