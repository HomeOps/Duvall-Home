"""Sensors for Samsung dongle appliances."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DongleCoordinator
from .entity import DongleEntity, parse_hhmmss


@dataclass(frozen=True, kw_only=True)
class DongleSensorDescription(SensorEntityDescription):
    """A sensor plus how to pull it out of the payload."""

    value_fn: Callable[[dict[str, Any], dict[str, Any]], Any]
    # Only create the entity if the appliance actually reports the field --
    # washers and dryers share a schema but populate different halves of it.
    exists_fn: Callable[[dict[str, Any], dict[str, Any]], bool]
    # Where the appliance advertises this field's vocabulary, e.g.
    # supportedProgress. Present => the sensor becomes a proper enum, which
    # gives HA long-term statistics (time-in-state) and translatable state
    # names. Absent => it stays a plain string sensor.
    options_fn: Callable[[dict[str, Any], dict[str, Any]], list[str] | None] | None = None


def slugify_state(value: str) -> str:
    """Samsung's CamelCase -> a Home Assistant enum slug.

    ``RinseHold`` -> ``rinse_hold``, ``None`` -> ``none``.

    Required, not cosmetic: hassfest rejects translation keys that are not
    ``[a-z0-9-_]+``, so an enum whose states keep the vendor casing cannot have
    translated names at all.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _op(key: str) -> Callable[[dict, dict], Any]:
    return lambda operation, _appliance: operation.get(key)


def _app(key: str) -> Callable[[dict, dict], Any]:
    return lambda _operation, appliance: appliance.get(key)


SENSORS: tuple[DongleSensorDescription, ...] = (
    DongleSensorDescription(
        key="state",
        translation_key="state",
        # Deliberately NOT an enum: the payload advertises no vocabulary for
        # this field. Only "Ready" and "Run" have been observed, and an enum
        # that meets an unlisted value breaks the entity mid-cycle.
        value_fn=_op("state"),
        exists_fn=lambda op, _a: "state" in op,
    ),
    DongleSensorDescription(
        key="progress",
        translation_key="progress",
        device_class=SensorDeviceClass.ENUM,
        options_fn=_op("supportedProgress"),
        value_fn=_op("progress"),
        exists_fn=lambda op, _a: "progress" in op,
    ),
    DongleSensorDescription(
        key="progress_percentage",
        translation_key="progress_percentage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_op("progressPercentage"),
        exists_fn=lambda op, _a: "progressPercentage" in op,
    ),
    DongleSensorDescription(
        key="remaining_time",
        translation_key="remaining_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        # Note: while idle this is the *estimate* for the selected cycle, not
        # a countdown. It only counts down once a cycle is running.
        value_fn=lambda op, _a: parse_hhmmss(op.get("remainingTime")),
        exists_fn=lambda op, _a: "remainingTime" in op,
    ),
    # --- dryer-specific ---
    DongleSensorDescription(
        key="dry_level",
        translation_key="dry_level",
        device_class=SensorDeviceClass.ENUM,
        options_fn=_app("supportedDryLevel"),
        value_fn=_app("dryLevel"),
        exists_fn=lambda _op, a: "dryLevel" in a,
    ),
    DongleSensorDescription(
        key="dry_time",
        translation_key="dry_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        value_fn=lambda _op, a: parse_hhmmss(a.get("dryTime")),
        exists_fn=lambda _op, a: "dryTime" in a,
    ),
    # --- washer-specific ---
    DongleSensorDescription(
        key="soil_level",
        translation_key="soil_level",
        device_class=SensorDeviceClass.ENUM,
        options_fn=_app("supportedSoilLevel"),
        value_fn=_app("soilLevel"),
        exists_fn=lambda _op, a: "soilLevel" in a,
    ),
    DongleSensorDescription(
        key="spin_level",
        translation_key="spin_level",
        device_class=SensorDeviceClass.ENUM,
        options_fn=_app("supportedSpinLevel"),
        value_fn=_app("spinLevel"),
        exists_fn=lambda _op, a: "spinLevel" in a,
    ),
    # --- shared ---
    DongleSensorDescription(
        key="water_temperature",
        translation_key="water_temperature",
        device_class=SensorDeviceClass.ENUM,
        options_fn=_app("supportedWaterTemperature"),
        value_fn=_app("waterTemperature"),
        exists_fn=lambda _op, a: "waterTemperature" in a,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create sensors the appliance actually reports."""
    coordinator: DongleCoordinator = hass.data[DOMAIN][entry.entry_id]
    device = coordinator.data or {}
    operation = device.get("Operation", {}) or {}
    appliance = device.get("Washer", {}) or {}

    async_add_entities(
        DongleSensor(coordinator, description)
        for description in SENSORS
        if description.exists_fn(operation, appliance)
    )


class DongleSensor(DongleEntity, SensorEntity):
    """One field of the appliance payload."""

    entity_description: DongleSensorDescription

    def __init__(
        self, coordinator: DongleCoordinator, description: DongleSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        value = self.entity_description.value_fn(self._operation, self._appliance)
        # Enum states are slugified to satisfy HA's translation key rules; the
        # plain string sensors keep the appliance's own casing.
        if self.entity_description.options_fn is not None and isinstance(value, str):
            return slugify_state(value)
        return value

    @property
    def options(self) -> list[str] | None:
        """Vocabulary for enum sensors, taken from the appliance itself.

        The current value is appended if the appliance ever reports something
        outside its own advertised list. An enum sensor whose state is not in
        ``options`` raises and goes unknown, and this protocol was reverse
        engineered from two appliances -- better to surface an unexpected value
        than to break the entity mid-cycle.
        """
        if self.entity_description.options_fn is None:
            return None
        advertised = self.entity_description.options_fn(self._operation, self._appliance)
        if not advertised:
            return None
        options = [slugify_state(option) for option in advertised]
        current = self.native_value
        if isinstance(current, str) and current not in options:
            options.append(current)
        return options

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Drop back to a plain sensor when the appliance advertises no vocabulary.

        ``device_class`` is fixed per description, but whether a vocabulary
        exists is per appliance -- an untested model may omit the
        ``supported*`` list entirely, and ENUM without options is invalid.
        """
        if self.entity_description.options_fn is not None and not self.options:
            return None
        return self.entity_description.device_class
