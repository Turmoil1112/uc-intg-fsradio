#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging
import os
import sys

import ucapi
from ucapi_framework import BaseIntegrationDriver

from config import RadioConfigManager, RadioDeviceConfig
from device import FrontierSiliconDevice
from media_player import FrontierSiliconMediaPlayer
from preset_button import FrontierSiliconPresetButton

from fsradio.framework_discovery import FrontierSiliconSSDPDiscovery
from setup_flow import FrontierSiliconSetupFlow


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class FrontierSiliconDriver(BaseIntegrationDriver[FrontierSiliconDevice, RadioDeviceConfig]):
    pass


async def main() -> None:
    # Logging setup
    if os.getenv("INVOCATION_ID"):
        handler = logging.StreamHandler()
        logging.basicConfig(handlers=[handler])
    else:
        logging.basicConfig(
            format="%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s.%(funcName)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    for logger_name in ("driver", "device", "setup_flow", "fsradio"):
        logging.getLogger(logger_name).setLevel(level)

    loop = asyncio.get_running_loop()

    # -------------------------
    # Driver init (IMPORTANT)
    # -------------------------
    driver = FrontierSiliconDriver(
        device_class=FrontierSiliconDevice,
        entity_classes=[
            lambda cfg, dev: FrontierSiliconMediaPlayer(cfg, dev),
            lambda cfg, dev: [
                FrontierSiliconPresetButton(cfg, dev, preset_name, idx)
                for idx, preset_name in enumerate(cfg.presets, start=1)
            ],
        ],
        loop=loop,
        driver_id="fsradio",
    )

    # -------------------------
    # Config Manager (CRITICAL)
    # -------------------------
    config_manager = RadioConfigManager(
        driver.api.config_dir_path,
        add_handler=driver.on_device_added,
        remove_handler=driver.on_device_removed,
        config_class=RadioDeviceConfig,
    )
    driver.config_manager = config_manager

    # -------------------------
    # SSDP Discovery (Framework)
    # -------------------------
    discovery = FrontierSiliconSSDPDiscovery(timeout=5)

    # -------------------------
    # Setup Flow (Unified API)
    # -------------------------
    setup_handler = FrontierSiliconSetupFlow.create_handler(
        driver,
        discovery=discovery,
    )

    # -------------------------
    # Register existing devices
    # -------------------------
    await driver.register_all_device_instances(connect=False)

    # -------------------------
    # Start UC API
    # -------------------------
    await driver.api.init("driver.json", setup_handler)

    # Keep process alive
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())