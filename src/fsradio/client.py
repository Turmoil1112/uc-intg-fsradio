from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
import aiohttp
import xml.etree.ElementTree as ET

from afsapi import AFSAPI
from ucapi import Pagination, media_player

_LOG = logging.getLogger("fsradio.client")

NAV_MEDIA_ID_PREFIX = "fsnav://"
NAV_MEDIA_TYPE = "frontier-silicon://nav"
STATION_MEDIA_TYPE = "frontier-silicon://station"


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
                    channel=metadata["media_album"],
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

    async def browse_media(self, options: media_player.BrowseOptions) -> media_player.BrowseResults:
        _LOG.debug(
            "browse_media media_id=%r media_type=%r page=%r limit=%r",
            getattr(options, "media_id", None),
            getattr(options, "media_type", None),
            getattr(getattr(options, "paging", None), "page", None),
            getattr(getattr(options, "paging", None), "limit", None),
        )
        async with self._lock:
            api = await self._ensure_api()
            try:
                path = _media_id_to_path(getattr(options, "media_id", None))
                if path:
                    await api.nav_select_folder_via_path(path)

                raw_items = await self._read_nav_items(api)

                paging = getattr(options, "paging", None)
                page = int(getattr(paging, "page", 1) or 1)
                limit = int(getattr(paging, "limit", 20) or 20)
                page = max(1, page)
                limit = max(1, limit)
                start = (page - 1) * limit
                end = start + limit

                children: list[media_player.BrowseMediaItem] = []
                for key, fields in raw_items[start:end]:
                    title = _nav_item_title(fields, key)
                    is_folder = _nav_item_is_folder(fields)
                    child_path = [*path, key]
                    children.append(
                        media_player.BrowseMediaItem(
                            media_id=_path_to_media_id(child_path),
                            title=title,
                            media_class=media_player.MediaClass.DIRECTORY
                            if is_folder
                            else media_player.MediaClass.RADIO,
                            media_type=NAV_MEDIA_TYPE if is_folder else STATION_MEDIA_TYPE,
                            can_browse=is_folder,
                            can_play=not is_folder,
                        )
                    )

                return media_player.BrowseResults(
                    media=media_player.BrowseMediaItem(
                        media_id=_path_to_media_id(path),
                        title="Radio Browser" if not path else "Stations",
                        media_class=media_player.MediaClass.DIRECTORY,
                        media_type=NAV_MEDIA_TYPE,
                        can_browse=True,
                        can_play=False,
                        items=children,
                    ),
                    pagination=Pagination(
                        count=len(raw_items),
                        limit=limit,
                        page=page,
                    ),
                )
            except Exception as exc:
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc

    async def play_media(self, media_id: str, media_type: str | None = None) -> None:
        _LOG.debug("play_media media_id=%s media_type=%s", media_id, media_type)

        if not media_id.startswith(NAV_MEDIA_ID_PREFIX):
            raise FrontierSiliconError(f"Unsupported media_id: {media_id}")

        path = _media_id_to_path(media_id)
        if not path:
            raise FrontierSiliconError("Cannot play root navigation item")

        async with self._lock:
            api = await self._ensure_api()
            try:
                _LOG.debug("Selecting FS navigation item path=%s", path)
                result = await api.nav_select_item_via_path(path)
                _LOG.debug("nav_select_item_via_path result=%s", result)

                if result is False:
                    raise FrontierSiliconError(f"Radio rejected media selection: {media_id}")

            except Exception as exc:
                _LOG.exception("Failed to select media_id=%s path=%s", media_id, path)
                await self._invalidate_api()
                raise FrontierSiliconError(str(exc)) from exc

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
        media_album: str | None = play_album

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

        elif source_cf in {"dab", "dab+"}:
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

        if not channel_name and self._last_selected_preset_id and source_cf in {"internet radio", "dab", "dab+", "fm"}:
            for preset in await self._fetch_presets(api):
                if preset.id == self._last_selected_preset_id:
                    channel_name = preset.name
                    break

        if channel_name:
            if not media_album or media_album.casefold() in {"unknown", "n/a"}:
                media_album = channel_name

        if not media_title and channel_name:
            media_title = channel_name

                                                                                                                            
                                                                                                          
                

        _LOG.debug(
            "metadata source=%s name=%r text=%r artist=%r album=%r image=%r -> channel=%r title=%r artist=%r album_out=%r",
            source,
            play_name,
            play_text,
            play_artist,
            play_album,
            play_image,
            channel_name,
            media_title,
            media_artist,
            media_album,
        )

        return {
            "media_title": media_title,
            "media_artist": media_artist,
            "media_album": media_album,
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

    async def _read_nav_items(self, api: AFSAPI) -> list[tuple[int, dict[str, Any]]]:
        cursor = "-1"
        max_items = 14
        all_items = []

        while True:
            batch = await self._list_get_next_nav(api, cursor, max_items)
            if not batch:
                break

            all_items.extend(batch)

            next_cursor = str(batch[-1][0])
            if next_cursor == cursor:
                break

            cursor = next_cursor

            if len(batch) < max_items:
                break

        return all_items

    async def _list_get_next_nav(
        self,
        api: AFSAPI,
        cursor: str,
        max_items: int,
    ) -> list[tuple[int, dict[str, Any]]]:
        root_url = self._base_url

        if root_url.endswith("/device"):
            root_url = root_url[: -len("/device")]

        url = (
            f"{root_url}/fsapi/LIST_GET_NEXT/"
            f"netRemote.nav.list/{cursor}"
            f"?pin={self._pin}&maxItems={max_items}"
        )

        _LOG.debug("LIST_GET_NEXT url=%s", url)

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=self._timeout) as response:
                response.raise_for_status()
                text = await response.text(encoding="utf-8", errors="replace")

        return _parse_nav_list_xml(text)

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
    
                                                        
                                                                                             


def _path_to_media_id(path: list[int]) -> str:
    if not path:
        return NAV_MEDIA_ID_PREFIX
    return NAV_MEDIA_ID_PREFIX + "/".join(str(item) for item in path)


def _media_id_to_path(media_id: str | None) -> list[int]:
    if not media_id:
        return []
    if media_id == NAV_MEDIA_ID_PREFIX:
        return []
    if not media_id.startswith(NAV_MEDIA_ID_PREFIX):
                     
                                          
                    
        return []
                                                                          

    rest = media_id[len(NAV_MEDIA_ID_PREFIX):].strip("/")
    if not rest:
        return []

    return [int(part) for part in rest.split("/") if part]
                                                      

                          
                                                                     
                                                    

def _normalize_nav_fields(fields: Any) -> dict[str, Any]:
    if isinstance(fields, dict):
        return dict(fields)
                               

    result: dict[str, Any] = {}
    for attr in ("name", "text", "title", "label", "type", "subtype", "item_type", "selectable"):
        if hasattr(fields, attr):
            value = getattr(fields, attr)
            if value is not None:
                result[attr] = value
    return result

                                                                                                                       

def _nav_item_title(fields: dict[str, Any], key: int) -> str:
    for attr in ("name", "text", "title", "label"):
        value = fields.get(attr)
        if value not in (None, ""):
            return str(value).strip()
    return f"Item {key}"
                                                                                                                 
                                                        
                                               
                                                                                                          
                     
                 

                                                                    

def _nav_item_is_folder(fields: dict[str, Any]) -> bool:
    item_type = str(
        fields.get("type")
        or fields.get("subtype")
        or fields.get("item_type")
        or ""
    ).strip().casefold()
                                                     
                                                     
                  
                                      
                                                   
                                
                              
                  
             
    
                                                                                                                      
                                               
                                                                           

    if item_type in {"folder", "directory", "container", "menu"}:
        return True
    if item_type in {"station", "track", "item", "playable", "preset"}:
        return False

    selectable = fields.get("selectable")
    if selectable is not None:
        return not bool(_to_bool(selectable))

    return item_type in {"0", "dir"}


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

def _parse_nav_list_xml(xml_text: str) -> list[tuple[int, dict[str, Any]]]:
    root = ET.fromstring(xml_text)
    items: list[tuple[int, dict[str, Any]]] = []

    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag != "item":
            continue

        key = item.attrib.get("key") or item.attrib.get("id")
        if key is None:
            continue

        fields: dict[str, Any] = {}

        for child in item:
            child_tag = child.tag.rsplit("}", 1)[-1]

            field_name = (
                child.attrib.get("name")
                or child.attrib.get("field")
                or child_tag
            )

            value = _xml_value(child)
            if field_name and value not in (None, ""):
                fields[field_name] = value

        normalized_key = _safe_int(key)
        if normalized_key is not None:
            _LOG.debug("parsed nav item key=%s fields=%s", normalized_key, fields)
            items.append((normalized_key, _normalize_nav_fields(fields)))

    return items


def _xml_value(elem: ET.Element) -> Any:
    text = (elem.text or "").strip()
    if text:
        return text

    for attr in (
        "value",
        "c8_array",
        "u32",
        "u16",
        "u8",
        "s32",
        "s16",
        "s8",
        "bool",
    ):
        value = elem.attrib.get(attr)
        if value not in (None, ""):
            return value

    # Manche FSAPI-Responses verschachteln den Wert noch einmal.
    for child in elem:
        child_value = _xml_value(child)
        if child_value not in (None, ""):
            return child_value

    return None