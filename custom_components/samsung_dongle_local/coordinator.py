"""Polling coordinator for a single Samsung dongle appliance."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DongleAuthError, DongleClient, DongleError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class DongleCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch ``/devices/<index>`` on an interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: DongleClient,
        index: int = 0,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {client.host}",
            update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        self.index = index
        self.information: dict[str, Any] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            device = await self.client.async_get_device(self.index)
        except DongleAuthError as err:
            # Tokens survive reboots and VLAN changes, so a rejection here
            # almost always means a factory/network reset wiped it. Re-pairing
            # needs physical access, so surface it as a reauth.
            raise ConfigEntryAuthFailed(str(err)) from err
        except DongleError as err:
            raise UpdateFailed(str(err)) from err

        if not self.information:
            try:
                self.information = await self.client.async_get_information(self.index)
            except DongleError:  # non-fatal: only used for device registry detail
                _LOGGER.debug("could not read /information for %s", self.client.host)

        return device
