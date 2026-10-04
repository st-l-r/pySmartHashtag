"""Provides an accessible control of the vehicle's door locks."""

import asyncio
import json
import logging
from typing import Any

from pysmarthashtag.account import SmartAccount
from pysmarthashtag.api import utils
from pysmarthashtag.api.client import SmartClient
from pysmarthashtag.const import API_TELEMATICS_URL
from pysmarthashtag.models import (
    SmartHumanCarConnectionError,
    SmartMainTokenExpiredError,
    SmartNonceError,
    SmartTokenRefreshNecessary,
    SmartVehicleNotInUseError,
)

_LOGGER = logging.getLogger(__name__)


class DoorLockControl:
    """Provides an accessible control of the vehicle's door locks."""

    LOCK_SERVICE_ID = "RDL_2"
    UNLOCK_SERVICE_ID = "RDU_2"
    MAX_RETRIES = 3

    def __init__(self, account: SmartAccount, vin: str):
        self.account = account
        self.config = account.config
        self.vin = vin
        self._command_lock = asyncio.Lock()

    def _get_payload(self, service_id: str, parameter: dict[str, str]) -> str:
        payload = {
            "creator": "tc",
            "command": "start",
            "operationScheduling": {
                "duration": 6,
                "interval": 0,
                "occurs": 1,
                "recurrentOperation": False,
            },
            "serviceId": service_id,
            "timestamp": utils.create_correct_timestamp(),
            "serviceParameters": [parameter],
        }
        return json.dumps(payload, separators=(",", ":"))

    @staticmethod
    def _command_succeeded(api_result: dict[str, Any]) -> bool:
        data = api_result.get("data")
        if not isinstance(data, dict):
            return False
        service_result = data.get("serviceResult")
        if not isinstance(service_result, dict):
            return False
        return (
            api_result.get("success") is True
            and str(api_result.get("code")) == "1000"
            and service_result.get("operationResult") == 1
            and service_result.get("error") is None
        )

    async def lock(self) -> bool:
        _LOGGER.debug("Locking all doors")
        return await self._send_command(
            self._get_payload(
                self.LOCK_SERVICE_ID,
                {"key": "door", "value": "all"},
            )
        )

    async def unlock(self) -> bool:
        _LOGGER.debug("Unlocking all doors")
        return await self._send_command(
            self._get_payload(
                self.UNLOCK_SERVICE_ID,
                {"key": "door", "value": "all"},
            )
        )

    async def _send_command(self, params: str) -> bool:
        await self.account._ensure_ssl_context()

        async with self._command_lock:
            async with SmartClient(self.config) as client:
                for retry in range(self.MAX_RETRIES):
                    try:
                        await self.account.select_active_vehicle(self.vin)

                        response = await client.put(
                            self.account.vehicles[self.vin].base_url
                            + API_TELEMATICS_URL
                            + self.vin,
                            headers={
                                **utils.generate_default_header(
                                    client.config.authentication.device_id,
                                    client.config.authentication.api_access_token,
                                    params={},
                                    method="PUT",
                                    url=API_TELEMATICS_URL + self.vin,
                                    body=params,
                                    vin=self.vin,
                                    model_code=self.account._vin_model_code(self.vin),
                                )
                            },
                            content=params.encode("utf-8"),
                        )

                        api_result = response.json()
                        if not isinstance(api_result, dict):
                            _LOGGER.warning(
                                "Door lock service returned a non-object response"
                            )
                            return False

                        return self._command_succeeded(api_result)

                    except (
                        SmartTokenRefreshNecessary,
                        SmartMainTokenExpiredError,
                    ):
                        _LOGGER.debug(
                            "Authentication expired; refreshing (retry %d/%d)",
                            retry + 1,
                            self.MAX_RETRIES,
                        )
                        await self.config.authentication.refresh()
                        continue

                    except (
                        SmartHumanCarConnectionError,
                        SmartVehicleNotInUseError,
                        SmartNonceError,
                    ) as err:
                        _LOGGER.debug(
                            "Transient door lock API error %s (retry %d/%d)",
                            type(err).__name__,
                            retry + 1,
                            self.MAX_RETRIES,
                        )
                        continue

        return False
