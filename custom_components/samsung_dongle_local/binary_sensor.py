"""Binary sensors for Samsung dongle appliances."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, IDLE_STATES, RUNNING_PROGRESS
from .coordinator import DongleCoordinator
from .entity import DongleEntity


@dataclass(frozen=True, kw_only=True)
class DongleBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor plus how to derive it."""

    value_fn: Callable[[dict[str, Any], dict[str, Any]], bool | None]
    exists_fn: Callable[[dict[str, Any], dict[str, Any]], bool]


def _is_running(operation: dict[str, Any], _appliance: dict[str, Any]) -> bool | None:
    """A cycle is under way.

    Derived from both fields because neither is sufficient alone: ``progress``
    reads ``None`` while idle *and* briefly at cycle start, and ``state`` reads
    ``Ready`` on an idle machine with a cycle merely selected.
    """
    progress = operation.get("progress")
    state = operation.get("state")
    if progress is None and state is None:
        return None
    if progress in RUNNING_PROGRESS:
        return True
    return state not in IDLE_STATES


BINARY_SENSORS: tuple[DongleBinarySensorDescription, ...] = (
    DongleBinarySensorDescription(
        key="running",
        translation_key="running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=_is_running,
        exists_fn=lambda op, _a: "state" in op or "progress" in op,
    ),
    DongleBinarySensorDescription(
        key="power",
        translation_key="power",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda op, _a: op.get("power") == "On",
        exists_fn=lambda op, _a: "power" in op,
    ),
    DongleBinarySensorDescription(
        key="child_lock",
        translation_key="child_lock",
        # "Ready" means unlocked. Worth surfacing: while Child Lock is engaged
        # Samsung disables Smart Control entirely, so pairing and any future
        # remote control silently stop working.
        value_fn=lambda op, _a: op.get("kidsLock") not in ("Ready", None),
        exists_fn=lambda op, _a: "kidsLock" in op,
    ),
    DongleBinarySensorDescription(
        key="wrinkle_prevent",
        translation_key="wrinkle_prevent",
        value_fn=lambda _op, a: a.get("wrinklePrevent") == "On",
        exists_fn=lambda _op, a: "wrinklePrevent" in a,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DongleCoordinator = hass.data[DOMAIN][entry.entry_id]
    device = coordinator.data or {}
    operation = device.get("Operation", {}) or {}
    appliance = device.get("Washer", {}) or {}

    async_add_entities(
        DongleBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if description.exists_fn(operation, appliance)
    )


class DongleBinarySensor(DongleEntity, BinarySensorEntity):
    """A derived boolean about the appliance."""

    entity_description: DongleBinarySensorDescription

    def __init__(
        self, coordinator: DongleCoordinator, description: DongleBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self._operation, self._appliance)
