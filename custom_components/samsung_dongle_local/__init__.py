"""Samsung Dongle (Local) -- local control of older Samsung appliances."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import DongleClient
from .const import CONF_CERT_PEM, CONF_KEY_PEM, CONF_TOKEN, DOMAIN
from .coordinator import DongleCoordinator
from .ssl_util import InvalidCertificate, build_contexts

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one appliance."""
    try:
        client_ctx, _server_ctx = await hass.async_add_executor_job(
            build_contexts, entry.data[CONF_CERT_PEM], entry.data.get(CONF_KEY_PEM)
        )
    except InvalidCertificate as err:
        raise ConfigEntryNotReady(f"certificate material unusable: {err}") from err

    client = DongleClient(
        entry.data[CONF_HOST], client_ctx, token=entry.data[CONF_TOKEN]
    )
    coordinator = DongleCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear one appliance down."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
    return unloaded
