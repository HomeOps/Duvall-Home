"""Diagnostics support for Local Things.

Downloadable from Settings > Devices & Services > this integration's
device > the menu > Download diagnostics. This is what the Repairs issue
(raised in coordinator.py when capability coverage is incomplete) points
users at: a redacted snapshot of the device's raw /device/0 state, plus
enough version/coverage metadata to reproduce and diagnose the gap.
"""
from __future__ import annotations

from importlib.metadata import version as pkg_version
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .registry.redact import redact_resources
from .registry.subdevices import MAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    integration = await async_get_integration(hass, DOMAIN)

    # importlib.metadata.version() reads the installed package's metadata off
    # disk (listdir + open + read_text), which trips HA's event-loop blocking
    # detector when called inline here. Offload it to the executor.
    stl_version = await hass.async_add_executor_job(pkg_version, "smartthings-local")

    # /oic/p, /oic/d, and /oic/res sit outside the /device/0 batch captured
    # below, so they'd otherwise never reach an issue report. /oic/d's `rt`
    # is OCF's standard device-type declaration; /oic/res is OCF's
    # discovery endpoint, listing every href/Collection the connection
    # hosts -- relevant to the "Composite Device" model (issue #177) where
    # a single physical device exposes more than one logical subdevice. See
    # registry/identity.py.
    identity = coordinator._identity

    def _seed_diag(su) -> dict:
        # A flat-mode subdevice (issue #205 -- no working /<uuid>/device/0
        # Collection, so its state comes from individually-polled hrefs
        # instead) has no meaningful seed_path; report the flat_hrefs list
        # in its place rather than the misleading bare "/" a joined empty
        # tuple would otherwise produce.
        return {
            "seed_path": ("/" + "/".join(su.seed_path)) if su.seed_path else None,
            "flat_hrefs": list(su.flat_hrefs),
        }

    def _subdevice_diag(su) -> dict:
        # One pass over coordinator.bound for both fields below (count and
        # the distinct hrefs), and one redaction of this subdevice's canonical
        # view -- `model` reads modelNum off the already-redacted `resources`
        # rather than redacting /information/vs/0 a second time. modelNum
        # itself never matches redact.py's substring rules, so which side of
        # redact_resources it's read from doesn't change the value.
        matching = [b for b in coordinator.bound if b.subdevice == su]
        res = redact_resources(coordinator.canonical_resources(su))
        return {
            "kind": su.kind,
            "key": su.key,
            **_seed_diag(su),
            "bound_entity_count": len(matching),
            "hrefs": sorted({b.href for b in matching}),
            "model": res.get('/information/vs/0', {}).get('x.com.samsung.da.modelNum', ''),
            # Keyed by this subdevice's *canonical* hrefs, not the real ones
            # it answers on -- '/mode/vs/0' rather than '/mode/vs/1' or
            # '/<uuid>/mode/vs/0'. That's the form the registry and every
            # capability are written against, so a sibling's block can be
            # read (or pasted into the skill's standalone-discovery
            # recipe) exactly like the master's `resources` above,
            # instead of having to be de-indexed by hand first.
            "resources": res,
        }

    return {
        "device_type": coordinator.device_type_name or "unknown",
        "one_ui_version": coordinator.one_ui_version,
        "identity": {
            "manufacturer": identity.manufacturer,
            "model": identity.model,
            "device_types": list(identity.device_types),
            "resources": redact_resources(identity.raw),
        } if identity is not None else None,
        "unbound_hrefs": sorted(coordinator._unbound_hrefs),
        # This subdevice's own resources, and only this subdevice's -- what
        # the module docstring and the adding-device-support skill have
        # always described it as ("the parsed /device/0 snapshot"). On a
        # composite device (issue #177) `last_resources` is the union across
        # every live subdevice keyed by real hrefs, so reporting it raw here
        # would mix a sibling's /mode/vs/1 in with the master's /mode/vs/0
        # under no attribution at all. Each sibling reports its own
        # resources in its own `subdevices` entry below instead. For a
        # device with no subdevices -- almost every device -- this is
        # byte-identical to `last_resources`.
        "resources": redact_resources(coordinator.canonical_resources(MAIN)),
        # Sibling indoor subdevices discovered on this connection (issue
        # #177) -- per-subdevice kind/key/seed path plus what actually bound
        # to it, so a report shows whether a composite device's subdevice
        # was found at all and what it resolved to. subdeviceIdList (the
        # UUID a prefixed subdevice's key comes from) is deliberately NOT
        # redacted here even
        # though the field matches redact.py's 'deviceid' substring rule
        # elsewhere in `resources` above -- it's an appliance-internal
        # pairing id, not account data, and reporting the key is what makes
        # this block actionable.
        "subdevices": [_subdevice_diag(su) for su in coordinator.subdevices],
        # Candidates that answered their seed but that discover_partitioned's
        # entity-level liveness gate rejected -- an unused SmartThings slot
        # (the issue #177 reporter's /device/2) that still answers a
        # same-shaped batch, not a real second subdevice. Reported alongside
        # subdevices above so a report shows what was found *and* why it
        # didn't become an entity, not just silence where a third climate
        # card might otherwise be expected.
        "subdevices_skipped": [
            {
                "kind": skip.subdevice.kind,
                "key": skip.subdevice.key,
                **_seed_diag(skip.subdevice),
                "hrefs": list(skip.hrefs),
                # The reps the liveness gate actually judged, canonicalized
                # like the materialized subdevices above. These are the one
                # thing a reader needs to second-guess a skip ("is my second
                # subdevice really absent, or did the gate get it wrong?"),
                # and they exist nowhere else in this dump: a rejected
                # candidate is never polled again and never enters the state
                # cache, so `resources` above cannot contain them by
                # construction.
                "resources": redact_resources({
                    canon: rep
                    for href, rep in coordinator._skipped_subdevice_resources.items()
                    if (canon := skip.subdevice.to_canonical(href)) is not None
                }),
            }
            for skip in coordinator._skipped_subdevices
        ],
        # What each enumeration probe returned ({} vs a batch), keyed by the
        # seed href attempted -- lets a report distinguish "checked, nothing
        # there" from "never checked", the same posture the speculative
        # /device/1 //device/2 probe this replaced used to document directly
        # in identity.py before it moved to registry/subdevices.py.
        "subdevice_probes": dict(sorted(coordinator._subdevice_probes.items())),
        # /multidevice/vs/0's rep ({} when the board doesn't answer it).
        # Reported on its own rather than inside `resources` because it is
        # metadata about the connection rather than state of any one
        # subdevice -- and because nothing polls it after discovery, so it
        # would go stale in there. Its numofsubdevice count is what
        # independently corroborates the subdevices/subdevices_skipped split
        # above.
        "multidevice": redact_resources(coordinator._multidevice),
        "integration_version": integration.version,
        "smartthings_local_version": stl_version,
        "observe_mode": coordinator.observe_mode,
        "observe_subscribed_hrefs": sorted(coordinator._observe.subscribed_hrefs),
        "observe_fallback_hrefs": sorted(coordinator._observe.fallback_hrefs),
        "observe_last_mode_change": coordinator._observe.last_mode_change_wall,
        "observe_href_freshness_s": {
            href: coordinator._cache.freshness_s(href)
            for href in coordinator._observe.subscribed_hrefs
        },
    }
