"""
This component provides support for Home Automation Manager (HAM).
For more details about this component, please refer to the documentation at
https://home-assistant.io/components/edgeos/
"""
import sys
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .device_manager import DeviceManager
from .entity_manager import EntityManager
from .EdgeOSData import EdgeOSData
from .const import *

_LOGGER = logging.getLogger(__name__)


class EdgeOSHomeAssistant:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self._hass = hass

        self._config_entry = entry

        self._integration_name = entry.data.get(CONF_NAME)
        self._unit = entry.data.get(CONF_UNIT, ATTR_BYTE)
        self._unit_size = ALLOWED_UNITS.get(self._unit, BYTE)

        self._remove_async_track_time_api = None
        self._remove_async_track_time_entities = None

        self._is_first_time_online = True
        self._is_initialized = False
        self._is_ready = False
        self._data_manager = None
        self._entity_manager = None
        self._device_manager = None

        self._services = {
            "stop": self.service_stop,
            "restart": self.service_restart,
            "save_debug_data": self.service_save_debug_data,
            "log_events": self.service_log_events
        }

        self._service_schema = {
            "log_events": SERVICE_LOG_EVENTS_SCHEMA
        }

    @property
    def data_manager(self) -> EdgeOSData:
        return self._data_manager

    @property
    def entity_manager(self) -> EntityManager:
        return self._entity_manager

    @property
    def device_manager(self) -> DeviceManager:
        return self._device_manager

    @property
    def unit(self):
        return self._unit

    @property
    def unit_size(self):
        return self._unit_size

    async def initialize(self):
        def finalize(event_time):
            self._hass.async_create_task(self.async_finalize(event_time))

        async_call_later(self._hass, 5, finalize)

    async def async_finalize(self, event_time):
        _LOGGER.debug(f"async_finalize called at {event_time}")

        # Register Service
        for service_name in self._services:
            service_callback = self._services[service_name]
            service_schema = self._service_schema.get(service_name)

            self._hass.services.async_register(DOMAIN, service_name, service_callback, schema=service_schema)

        self._data_manager = EdgeOSData(self._hass, self._config_entry.data, self.update)
        self._device_manager = DeviceManager(self._hass, self)
        self._entity_manager = EntityManager(self._hass, self)

        self._hass.async_create_task(self._data_manager.initialize())

        self._hass.async_create_task(self.async_update_entry(self._config_entry, False))

        def update_api(now):
            self._hass.async_create_task(self.async_update_api(now))

        self._remove_async_track_time_api = async_track_time_interval(self._hass,
                                                                      update_api,
                                                                      SCAN_INTERVAL_API)

        def update_entities(now):
            self._hass.async_create_task(self.async_update_entities(now))

        self._remove_async_track_time_entities = async_track_time_interval(self._hass,
                                                                           update_entities,
                                                                           SCAN_INTERVAL_ENTITIES)

        self._is_initialized = True

    async def async_remove(self):
        _LOGGER.debug(f"async_remove called")

        self.service_stop(None)

        # Unregister Service
        for service_name in self._services:
            self._hass.services.async_remove(DOMAIN, service_name)

        if self._remove_async_track_time_api is not None:
            self._remove_async_track_time_api()

        if self._remove_async_track_time_entities is not None:
            self._remove_async_track_time_entities()

        unload = self._hass.config_entries.async_forward_entry_unload

        for domain in SIGNALS:
            self._hass.async_create_task(unload(self._config_entry, domain))

    async def async_update_entry(self, entry, clear_all):
        _LOGGER.info(f"async_update_entry: {self._config_entry.options}")
        self._is_ready = False

        self._config_entry = entry

        self._entity_manager.update_options(entry.options)

        if clear_all:
            await self._device_manager.async_remove_entry(self._config_entry.entry_id)

            self._data_manager.update(True)

            await self.discover_all()
        else:
            await self.async_update_api(None)

    async def async_update_api(self, event_time):
        if not self._is_initialized:
            _LOGGER.info(f'NOT INITIALIZED, cannot update data from API: {event_time}')

            return

        _LOGGER.debug(f'Update API: {event_time}')

        await self._data_manager.refresh()

    async def async_update_entities(self, event_time):
        if not self._is_initialized:
            _LOGGER.info(f'NOT INITIALIZED, cannot update entities: {event_time}')

            return

        if self._is_first_time_online:
            self._is_first_time_online = False

            await self.async_update_entry(self._config_entry, False)

        await self.discover_all()

    def update(self):
        try:
            default_device_info = self.device_manager.get(DEFAULT_NAME)

            if CONF_NAME in default_device_info:
                self.entity_manager.update()

            self._is_ready = True
        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f'Failed to update, Error: {ex}, Line: {line_number}')

    async def discover_all(self):
        if not self._is_ready or not self._is_initialized:
            return

        self.device_manager.update()

        default_device_info = self.device_manager.get(DEFAULT_NAME)

        if CONF_NAME in default_device_info:
            for domain in SIGNALS:
                await self.discover(domain)

            self.entity_manager.clear_domain_states()

    async def discover(self, domain):
        signal = SIGNALS.get(domain)

        if signal is None:
            _LOGGER.error(f"Cannot discover domain {domain}")
            return

        unload = self._hass.config_entries.async_forward_entry_unload
        setup = self._hass.config_entries.async_forward_entry_setup

        entry = self._config_entry

        can_unload = self.entity_manager.get_domain_state(domain, DOMAIN_UNLOAD)
        can_load = self.entity_manager.get_domain_state(domain, DOMAIN_LOAD)
        can_notify = not can_load and not can_unload

        if can_unload:
            _LOGGER.info(f"Unloading domain {domain}")

            self._hass.async_create_task(unload(entry, domain))
            self.entity_manager.set_domain_state(domain, DOMAIN_LOAD, False)

        if can_load:
            _LOGGER.info(f"Loading domain {domain}")

            self._hass.async_create_task(setup(entry, domain))
            self.entity_manager.set_domain_state(domain, DOMAIN_UNLOAD, False)

        if can_notify:
            async_dispatcher_send(self._hass, signal)

    def service_stop(self, service):
        _LOGGER.debug(f'Stop: {service}')

        self._hass.async_create_task(self._data_manager.terminate())

    def service_restart(self, service):
        _LOGGER.debug(f'Start: {service}')

        self._hass.async_create_task(self.async_update_entry(self._config_entry, False))

    def service_save_debug_data(self, service):
        _LOGGER.debug(f'Save Debug Data: {service}')

        try:
            path = self._hass.config.path(EDGEOS_DATA_LOG)

            with open(path, 'w+') as out:
                out.write(str(self.data_manager.edgeos_data))

        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f'Failed to log EdgeOS data, Error: {ex}, Line: {line_number}')

    def service_log_events(self, service):
        _LOGGER.debug(f'Log Events: {service}')

        enabled = service.data.get(ATTR_ENABLED, False)

        self._data_manager.log_events(enabled)


def _get_ha_data(hass, name) -> EdgeOSHomeAssistant:
    ha = hass.data[DATA_EDGEOS]
    ha_data = None

    if ha is not None:
        ha_data = ha.get(name)

    return ha_data
