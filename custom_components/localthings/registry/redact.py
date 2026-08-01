"""Redact account/identity data from a raw resource tree before it leaves
the user's Home Assistant instance (diagnostics downloads, issue reports).

/device/0 dumps mix appliance state with genuinely sensitive data when
Bixby/voice is set up on the device: a Samsung account email, a Bixby
access token, a hashed device ID, WiFi/BLE MAC addresses, the serial
number, and otnDUID. This walks the whole tree and redacts any value whose
key matches a known-sensitive substring, regardless of which href it's
under — new device types will have unknown-shaped data we can't fully
enumerate in advance, so this errs on catching the field by name rather
than only redacting inside hrefs we already recognize.
"""
from __future__ import annotations

REDACTED = "**REDACTED**"

_SENSITIVE_SUBSTRINGS = (
    'mac', 'serial', 'token', 'login', 'account', 'email',
    'userid', 'deviceid', 'uuid', 'duid', 'password', 'secret',
)

# Matched whole, not as substrings. OCF's /oic/d and /oic/p identify the unit
# with bare one- and two-letter keys that the rules above cannot see, being
# far too short to match on -- 'di' alone is a substring of 'condition',
# 'display', 'dispenser' and plenty of other ordinary appliance fields:
#
#   'di' -- device UUID, 'pi' -- platform UUID. As identifying as the serial
#           number above.
#   'n'  -- /oic/d's device name. Free text the owner can set from the
#           SmartThings app, so it may well carry a person's name. Nothing
#           in the /device/0 dump has ever exposed it; it only became
#           reachable when diagnostics started reporting /oic/d, and the
#           device-type signal we actually want from that resource is `rt`,
#           which is not redacted.
_SENSITIVE_EXACT = frozenset({'di', 'pi', 'n'})


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT:
        return True
    return any(s in lowered for s in _SENSITIVE_SUBSTRINGS)


def redact_resources(resources):
    """Recursively redact dict values whose key matches a sensitive substring.

    Works on the shape produced by parse_device0_batch (dict[href, rep]) or
    any nested dict/list structure within a rep.
    """
    if isinstance(resources, dict):
        return {
            key: (REDACTED if _is_sensitive_key(key) else redact_resources(value))
            for key, value in resources.items()
        }
    if isinstance(resources, list):
        return [redact_resources(item) for item in resources]
    return resources
