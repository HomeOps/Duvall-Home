"""
Support for Ubiquiti EdgeOS routers.
HEAVILY based on the AsusWRT component
For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/device_tracker.edgeos/
"""
import logging
import sys
from typing import Optional

from homeassistant.components.device_tracker import ATTR_SOURCE_TYPE, SOURCE_TYPE_ROUTER
from homeassistant.components.device_tracker.config_entry import ScannerEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import device_registry as dr

from .const import *
from .home_assistant import _get_ha_data

_LOGGER = logging.getLogger(__name__)
DEPENDENCIES = [DOMAIN]

CURRENT_DOMAIN = DOMAIN_DEVICE_TRACKER


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up EdgeOS based off an entry."""
    _LOGGER.debug(f"Starting async_setup_entry {CURRENT_DOMAIN}")

    try:
        entry_data = entry.data
        edgeos_name = entry_data.get(CONF_NAME)
        entities = []

        data = _get_ha_data(hass, edgeos_name)

        if data is not None:
            entities_data = data.get_entities(CURRENT_DOMAIN)
            for entity_name in entities_data:
                entity_data = entities_data.get(entity_name)

                entity = EdgeOSScanner(hass, edgeos_name, entity_data)

                _LOGGER.debug(f"Setup {CURRENT_DOMAIN}: {entity.name} | {entity.unique_id}")

                entities.append(entity)

        data.set_domain_entities_state(CURRENT_DOMAIN, True)

        async_add_entities(entities, True)
    except Exception as ex:
        exc_type, exc_obj, tb = sys.exc_info()
        line_number = tb.tb_lineno

        _LOGGER.error(f"Failed to load {CURRENT_DOMAIN}, error: {ex}, line: {line_number}")

    return True


async def async_unload_entry(hass, config_entry):
    _LOGGER.info(f"async_unload_entry {CURRENT_DOMAIN}: {config_entry}")

    entry_data = config_entry.data
    edgeos_name = entry_data.get(CONF_NAME)

    data = _get_ha_data(hass, edgeos_name)
    data.set_domain_entities_state(CURRENT_DOMAIN, False)

    return True


class EdgeOSScanner(ScannerEntity):
    """Represent a tracked device."""

    def __init__(self, hass, edgeos_name, entity):
        super().__init__()

        """Set up EdgeOS entity."""
        self._hass = hass
        self._edgeos_name = edgeos_name
        self._remove_dispatcher = None
        self._entity = entity

    @property
    def unique_id(self) -> Optional[str]:
        """Return the name of the node."""
        return f"{DEFAULT_NAME}-{CURRENT_DOMAIN}-{self.name}"

    @property
    def device_info(self):
        return {
            "identifiers": {
                (DOMAIN, self.unique_id)
            },
            "name": self.name,
            "manufacturer": MANUFACTURER,
            "model": DEFAULT_NAME
        }

    @property
    def device_state_attributes(self):
        """Return device specific attributes."""
        return self._entity.get(ENTITY_ATTRIBUTES, {})

    @property
    def name(self):
        """Return the name of the device."""
        return self._entity.get(ENTITY_NAME)

    @property
    def is_connected(self):
        """Return true if the device is connected to the network."""
        return self._entity.get(ENTITY_STATE, False)

    @property
    def should_poll(self):
        """No polling needed."""
        return False

    @property
    def source_type(self):
        """Return the source type, eg gps or router, of the device."""
        return self._entity.get(ATTR_SOURCE_TYPE, SOURCE_TYPE_ROUTER)

    async def async_added_to_hass(self):
        """Call when entity about to be added to Home Assistant."""
        await super().async_added_to_hass()

        self._remove_dispatcher = async_dispatcher_connect(self._hass,
                                                           SIGNALS[CURRENT_DOMAIN],
                                                           self.update_data)

    async def async_will_remove_from_hass(self):
        """Call when entity is being removed from hass."""
        await super().async_will_remove_from_hass()

        if self._remove_dispatcher:
            self._remove_dispatcher()

    @callback
    def update_data(self):
        self.hass.async_add_job(self.async_update_data)

    async def async_update_data(self):
        """Mark the device as seen."""
        _LOGGER.debug(f"{CURRENT_DOMAIN} update_data: {self.name} | {self.unique_id}")

        data = _get_ha_data(self._hass, self._edgeos_name)
        self._entity = data.get_entity(CURRENT_DOMAIN, self.name)

        if self._entity is None:
            self._entity = {}
            await self.async_remove()

            dev_id = self.device_info.get("id")
            device_reg = await dr.async_get_registry(self._hass)

            device_reg.async_remove_device(dev_id)
        else:
            self.async_schedule_update_ha_state(True)
