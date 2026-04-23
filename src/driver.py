#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import suppress
from typing import Any

import ucapi
from ucapi.media_player import Attributes as MediaAttr

import config
import media_player
import setup_flow
from fsradio.client import FrontierSiliconClient

_LOG = logging.getLogger("driver")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)
api = ucapi.IntegrationAPI(_LOOP)

_clients: dict[str, FrontierSiliconClient] = {}
_poll_tasks: dict[str, asyncio.Task[Any]] = {}


@api.listens_to(ucapi.Events.CONNECT)
async def on_connect() -> None:
    _LOG.debug("Client connected")
    await api.set_device_state(ucapi.DeviceStates.CONNECTED)
    for device in config.devices.all():
        _ensure_runtime(device)


@api.listens_to(ucapi.Events.DISCONNECT)
async def on_disconnect() -> None:
    _LOG.debug("Client disconnected")
    await _stop_all_pollers_and_clients()


@api.listens_to(ucapi.Events.ENTER_STANDBY)
async def on_standby() -> None:
    _LOG.debug("Remote entered standby")
    await _stop_all_pollers_and_clients()


@api.listens_to(ucapi.Events.EXIT_STANDBY)
async def on_exit_standby() -> None:
    _LOG.debug("Remote exited standby")
    for device in config.devices.all():
        _ensure_runtime(device)


@api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
async def on_subscribe_entities(entity_ids: list[str]) -> None:
    _LOG.debug("Subscribe entities: %s", entity_ids)
    for entity_id in entity_ids:
        device_id = config.device_from_entity_id(entity_id)
        device = config.devices.get(device_id)
        if device:
            _ensure_runtime(device)


@api.listens_to(ucapi.Events.UNSUBSCRIBE_ENTITIES)
async def on_unsubscribe_entities(entity_ids: list[str]) -> None:
    _LOG.debug("Unsubscribe entities: %s", entity_ids)
    subscribed = {config.device_from_entity_id(item.get("entity_id", "")) for item in api.configured_entities.get_all()}
    for entity_id in entity_ids:
        device_id = config.device_from_entity_id(entity_id)
        if device_id not in subscribed:
            await _stop_runtime(device_id)


async def _poll_device(device: config.RadioDevice) -> None:
    device_id = device.id
    client = _clients[device_id]
    entity = api.available_entities.get(device_id)
    if entity is None:
        return

    while True:
        try:
            state = await client.get_state()
            attrs = entity.attributes_from_state(state)
            api.configured_entities.update_attributes(device_id, attrs)
        except Exception as exc:  # pragma: no cover - device/lib dependent
            _LOG.warning("[%s] polling failed: %s", device.name, exc)
            api.configured_entities.update_attributes(device_id, {MediaAttr.STATE: "unavailable"})
        await asyncio.sleep(1.0)


def _stop_poller(device_id: str) -> None:
    task = _poll_tasks.pop(device_id, None)
    if task:
        task.cancel()


async def _stop_runtime(device_id: str) -> None:
    _stop_poller(device_id)
    client = _clients.pop(device_id, None)
    if client is not None:
        with suppress(Exception):
            await client.close()


async def _stop_all_pollers_and_clients() -> None:
    for device_id in list(_poll_tasks):
        _stop_poller(device_id)
    for device_id in list(_clients):
        await _stop_runtime(device_id)


def _register_entity(device: config.RadioDevice, client: FrontierSiliconClient) -> None:
    entity = media_player.FrontierSiliconMediaPlayer(device, client)
    if api.available_entities.contains(entity.id):
        api.available_entities.remove(entity.id)
    api.available_entities.add(entity)


def _ensure_runtime(device: config.RadioDevice) -> None:
    if device.id not in _clients:
        _clients[device.id] = FrontierSiliconClient(device.base_url, device.pin, device.timeout)
    _register_entity(device, _clients[device.id])
    if device.id not in _poll_tasks:
        _poll_tasks[device.id] = _LOOP.create_task(_poll_device(device))


def on_device_added(device: config.RadioDevice) -> None:
    _LOG.info("Configured radio added: %s", device.name)
    _ensure_runtime(device)


def on_device_removed(device: config.RadioDevice | None) -> None:
    if device is None:
        _LOG.info("All radios removed from configuration")
        _LOOP.create_task(_stop_all_pollers_and_clients())
        api.available_entities.clear()
        api.configured_entities.clear()
        return

    _LOG.info("Configured radio removed: %s", device.name)
    _LOOP.create_task(_stop_runtime(device.id))
    api.available_entities.remove(device.id)
    api.configured_entities.remove(device.id)


class JournaldFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        priority = {
            logging.DEBUG: "<6>",
            logging.INFO: "<5>",
            logging.WARNING: "<4>",
            logging.ERROR: "<3>",
            logging.CRITICAL: "<2>",
        }.get(record.levelno, "<6>")
        return f"{priority}{record.name}: {record.getMessage()}"


async def main() -> None:
    if os.getenv("INVOCATION_ID"):
        handler = logging.StreamHandler()
        handler.setFormatter(JournaldFormatter())
        logging.basicConfig(handlers=[handler])
    else:
        logging.basicConfig(
            format="%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s.%(funcName)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    for logger_name in ("driver", "config", "discover", "setup_flow", "media_player", "fsradio.client"):
        logging.getLogger(logger_name).setLevel(level)

    config.devices = config.Devices(api.config_dir_path, on_device_added, on_device_removed)
    for device in config.devices.all():
        _ensure_runtime(device)

    await api.init("driver.json", setup_flow.driver_setup_handler)


if __name__ == "__main__":
    try:
        _LOOP.run_until_complete(main())
        _LOOP.run_forever()
    finally:
        _LOOP.run_until_complete(_stop_all_pollers_and_clients())
        with suppress(Exception):
            _LOOP.run_until_complete(asyncio.sleep(0))
