"""Constants for the Samsung Dongle (Local) integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "samsung_dongle_local"

CONF_CERT_PEM = "cert_pem"
CONF_KEY_PEM = "key_pem"
CONF_TOKEN = "token"
CONF_DEVICE_INDEX = "device_index"

DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

# Appliance "type" values seen in the wild, mapped to a friendly label.
DEVICE_TYPES = {
    "Washer": "Washer",
    "Dryer": "Dryer",
    "Oven": "Oven",
    "Refrigerator": "Refrigerator",
    "AirConditioner": "Air Conditioner",
}

# Operation.progress values that mean "a cycle is under way".
RUNNING_PROGRESS = {"Weightsensing", "Wash", "Rinse", "Spin", "Drying", "Cooling"}

# Operation.state values that mean idle.
IDLE_STATES = {"Ready", "None", ""}
