"""Shared entity base + payload helpers."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DongleCoordinator


def parse_hhmmss(value: Any) -> int | None:
    """``"01:15:00"`` -> minutes. Returns None on anything unexpected."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(p) for p in parts)
    except ValueError:
        return None
    return hours * 60 + minutes + (1 if seconds >= 30 else 0)


class DongleEntity(CoordinatorEntity[DongleCoordinator]):
    """Base for every entity backed by one appliance."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DongleCoordinator, key: str) -> None:
        super().__init__(coordinator)
        device = coordinator.data or {}
        uuid = device.get("uuid") or coordinator.client.host
        self._attr_unique_id = f"{uuid}_{key}"

        info = coordinator.information or {}
        model = info.get("modelID") or device.get("description") or "Samsung appliance"
        # modelID looks like "DONGLE_WF7500|00185441|" -- keep the useful half.
        model = model.split("|")[0]

        sw_version = None
        for version in info.get("Versions", []):
            if version.get("type") == "Software":
                sw_version = version.get("number")

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(uuid))},
            manufacturer=info.get("manufacturer") or "Samsung Electronics",
            model=model,
            name=device.get("name") or device.get("type") or "Samsung appliance",
            sw_version=sw_version,
            configuration_url=f"https://{coordinator.client.host}:8888/devices",
        )

    @property
    def _device(self) -> dict[str, Any]:
        return self.coordinator.data or {}

    @property
    def _operation(self) -> dict[str, Any]:
        return self._device.get("Operation", {}) or {}

    @property
    def _appliance(self) -> dict[str, Any]:
        """The device-specific block.

        Confusingly, Samsung labels this ``Washer`` on *both* washers and
        dryers -- the dryer just puts ``dryLevel``/``dryTime`` in it where the
        washer puts ``soilLevel``/``spinLevel``.
        """
        return self._device.get("Washer", {}) or {}

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data)
