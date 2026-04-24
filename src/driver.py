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
from setup_flow import FrontierSiliconSetupFlow


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class FrontierSiliconDriver(BaseIntegrationDriver[FrontierSiliconDevice, RadioDeviceConfig]):
    pass


async def main() -> None:
    if os.getenv("INVOCATION_ID"):
        handler = logging.StreamHandler()
        logging.basicConfig(handlers=[handler])
    else:
        logging.basicConfig(
            format="%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s.%(funcName)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    for logger_name in ("driver", "device", "setup_flow", "media_player", "fsradio.client", "discover"):
        logging.getLogger(logger_name).setLevel(level)

    loop = asyncio.get_running_loop()

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

    config_manager = RadioConfigManager(
        driver.api.config_dir_path,
        add_handler=driver.on_device_added,
        remove_handler=driver.on_device_removed,
        config_class=RadioDeviceConfig,
    )
    driver.config_manager = config_manager

    setup_flow = FrontierSiliconSetupFlow(driver=driver, config_manager=config_manager)
    driver.setup_flow = setup_flow

    async def driver_setup_handler(msg: ucapi.SetupDriver) -> ucapi.SetupAction:
        if isinstance(msg, ucapi.DriverSetupRequest):
            return await setup_flow.handle_driver_setup(msg)
        if isinstance(msg, ucapi.UserDataResponse):
            return await setup_flow.handle_user_data_response(msg)
        return ucapi.SetupError()

    await driver.api.init("driver.json", driver_setup_handler)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
