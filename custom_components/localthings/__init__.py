"""Local Things — Samsung appliance local control integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS
from .coordinator import LocalThingsCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    coordinator = LocalThingsCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Cannot connect to device: {err}") from err
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry,
) -> bool:
    """Allow deleting a device this entry no longer provides (issue #214).

    Defining this at all is what makes Home Assistant offer the "Delete
    device" action for our devices; without it a device registry entry
    belonging to a loaded config entry can never be removed from the UI. That
    matters because a subdevice's HA device outlives the discovery that
    created it: a candidate that materialized under an older release (issue
    #214's phantom second air conditioner, born from an unused /device/1 slot
    reporting the appliance's energy counter -- see
    registry/subdevices.py's liveness gate) leaves a device entry behind that
    nothing recreates and nothing cleans up once the gate stops materializing
    it. Same for a sibling that a firmware update stops exposing.

    Removal is refused for devices this entry *does* currently provide --
    HA would recreate them on the next entity add, so allowing it would look
    like the delete silently failed. Deliberately no automatic pruning at
    discovery time: subdevice enumeration is one-shot and a sibling can fail
    to answer for a poll (issue #205 is exactly that on the reference
    hardware), so auto-removal would throw away a real subdevice's name,
    area and automation references on a transient miss. The user gets the
    button; the integration doesn't guess.
    """
    coordinator: LocalThingsCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        # Entry not loaded (or already unloaded) -- nothing is claiming this
        # device, so there's nothing to protect it from being removed.
        return True
    live = set(coordinator.device_info.get('identifiers') or set())
    for subdevice in coordinator.subdevices:
        live |= set(coordinator.device_info_for(subdevice).get('identifiers') or set())
    return not (device.identifiers & live)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: LocalThingsCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_close()
    return unloaded
