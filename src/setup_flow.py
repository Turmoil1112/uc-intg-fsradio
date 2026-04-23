from __future__ import annotations

import asyncio
import logging
from enum import IntEnum
from urllib.parse import urlparse

import ucapi
from ucapi import (
    AbortDriverSetup,
    DriverSetupRequest,
    IntegrationSetupError,
    RequestUserInput,
    SetupAction,
    SetupComplete,
    SetupDriver,
    SetupError,
    UserDataResponse,
)

import config
from fsradio.client import FrontierSiliconClient
from fsradio.discovery import DiscoveredRadio, discover_radios

_LOG = logging.getLogger("setup_flow")


class SetupSteps(IntEnum):
    INIT = 0
    DISCOVER = 1
    DEVICE_CHOICE = 2
    PIN = 3


_setup_step = SetupSteps.INIT
_discovered: list[DiscoveredRadio] = []
_pending_choice: DiscoveredRadio | None = None


_user_input_discovery = RequestUserInput(
    {"en": "Discover radio", "de": "Radio finden"},
    [
        {
            "id": "address",
            "label": {"en": "IP address (optional)", "de": "IP-Adresse (optional)"},
            "field": {"text": {"value": ""}},
        },
        {
            "id": "timeout",
            "label": {"en": "HTTP timeout in seconds", "de": "HTTP-Timeout in Sekunden"},
            "field": {"number": {"value": 2}},
        },
    ],
)


async def driver_setup_handler(msg: SetupDriver) -> SetupAction:
    global _setup_step
    if isinstance(msg, DriverSetupRequest):
        _setup_step = SetupSteps.DISCOVER
        if not msg.reconfigure:
            config.devices.clear()
        await asyncio.sleep(0.25)
        return _user_input_discovery

    if isinstance(msg, UserDataResponse):
        if _setup_step == SetupSteps.DISCOVER:
            return await _handle_discovery(msg)
        if _setup_step == SetupSteps.DEVICE_CHOICE:
            return await _handle_choice(msg)
        if _setup_step == SetupSteps.PIN:
            return await _handle_pin(msg)

    if isinstance(msg, AbortDriverSetup):
        _LOG.info("Setup aborted: %s", msg.error)
        _setup_step = SetupSteps.INIT
        return SetupError(error_type=IntegrationSetupError.CANCELED)

    return SetupError(error_type=IntegrationSetupError.OTHER)


async def _handle_discovery(msg: UserDataResponse) -> SetupAction:
    global _discovered, _setup_step
    address = str(msg.input_values.get("address", "")).strip()
    timeout = float(msg.input_values.get("timeout", 2) or 2)

    if address:
        radio = DiscoveredRadio(address=address, location=f"http://{address}:80/device", usn=address, server="", st="")
        _discovered = [radio]
    else:
        _discovered = await discover_radios(timeout=3.0)

    if not _discovered:
        return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

    items = []
    for radio in _discovered:
        label = radio.name or radio.address
        items.append({"id": radio.address, "label": {"en": f"{label} [{radio.address}]"}})

    _setup_step = SetupSteps.DEVICE_CHOICE
    return RequestUserInput(
        {"en": "Choose radio", "de": "Radio auswählen"},
        [
            {
                "id": "choice",
                "label": {"en": "Discovered radio", "de": "Gefundenes Radio"},
                "field": {"dropdown": {"value": items[0]["id"], "items": items}},
            },
            {
                "id": "timeout",
                "label": {"en": "HTTP timeout in seconds", "de": "HTTP-Timeout in Sekunden"},
                "field": {"number": {"value": timeout}},
            },
        ],
    )


async def _handle_choice(msg: UserDataResponse) -> SetupAction:
    global _pending_choice, _setup_step
    choice = str(msg.input_values["choice"])
    for item in _discovered:
        if item.address == choice:
            _pending_choice = item
            break
    if _pending_choice is None:
        return SetupError(error_type=IntegrationSetupError.OTHER)

    _setup_step = SetupSteps.PIN
    return RequestUserInput(
        {"en": "Enter radio PIN", "de": "Radio-PIN eingeben"},
        [
            {
                "id": "pin",
                "label": {"en": "PIN", "de": "PIN"},
                "field": {"text": {"value": "1234"}},
            },
            {
                "id": "timeout",
                "label": {"en": "HTTP timeout in seconds", "de": "HTTP-Timeout in Sekunden"},
                "field": {"number": {"value": float(msg.input_values.get("timeout", 2) or 2)}},
            },
        ],
    )


async def _handle_pin(msg: UserDataResponse) -> SetupAction:
    global _pending_choice, _setup_step
    if _pending_choice is None:
        return SetupError(error_type=IntegrationSetupError.OTHER)

    pin = int(str(msg.input_values["pin"]).strip())
    timeout = float(msg.input_values.get("timeout", 2) or 2)
    client = FrontierSiliconClient(_pending_choice.base_url, pin, timeout)

    try:
        name = await client.test_connection()
    except Exception as exc:
        _LOG.warning("Failed to validate radio %s: %s", _pending_choice.address, exc)
        return SetupError(error_type=IntegrationSetupError.AUTHORIZATION_ERROR)

    existing = config.devices.get_by_address(_pending_choice.address)
    if existing:
        config.devices.remove(existing.id)

    device = config.create_device(
        name=name,
        address=_pending_choice.address,
        base_url=_pending_choice.base_url,
        pin=pin,
        timeout=timeout,
    )
    config.devices.add(device)
    _pending_choice = None
    _setup_step = SetupSteps.INIT
    return SetupComplete()
