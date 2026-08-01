"""Runtime discovery: device resources -> bound entities.

A device advertises a set of OCF resources keyed by href. For every href
present in the registry, emit the capability's entities bound to that resource.
Unknown hrefs are a coverage gap; each one is passed to the optional `log`
callback (as the raw href, not a formatted message) and otherwise skipped.

An href with a registered capability whose rt_filter/match_fn declines for
this particular device (e.g. a filter capability sitting on hardware that
doesn't have that filter) is *not* a gap — a maintainer already looked at
that href and decided how to handle it. Only hrefs absent from the registry
entirely are reported.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .capability import Capability
from .entities import SamsungEntityDescription
from .subdevices import MAIN, Subdevice


@dataclass
class BoundEntity:
    href: str
    capability: Capability
    desc: SamsungEntityDescription
    instance: str = ''
    key_override: Optional[str] = None
    instance_name: Optional[str] = None
    # Which logical indoor subdevice (issue #177) this entity belongs to.
    # `href` above is always the *actual*, on-the-wire href for that
    # subdevice -- MAIN's
    # to_actual is the identity transform, so every device with no subdevices
    # behaves exactly as before this field existed.
    subdevice: Subdevice = MAIN


def _snake_to_title(s: str) -> str:
    """'CUBED_ICE'/'cubed_ice' -> 'Cubed Ice'. Shared with entity.py's
    _derive_name, which applies the same transform to an href-derived key."""
    return s.replace('_', ' ').title()


def _instance_name(cap: Capability, rep: dict) -> Optional[str]:
    """Normalize `cap.name_field`'s raw value ("CUBED_ICE" -> "Cubed Ice")
    for use as a display-name prefix, or None if the cap doesn't declare
    one or the device didn't report it."""
    if not cap.name_field:
        return None
    raw = rep.get(cap.name_field)
    if not isinstance(raw, str) or not raw:
        return None
    return _snake_to_title(raw)


def instance_suffix(href: str) -> str:
    """'' for the index-0 instance, else '_<n>' from the trailing segment."""
    tail = href.rstrip('/').rsplit('/', 1)[-1]
    if tail.isdigit() and tail != '0':
        return f'_{tail}'
    return ''


def _bind(cap: Capability, href: str, inst: str, inst_name: Optional[str],
          key_prefix: Optional[str] = None,
          subdevice: Subdevice = MAIN) -> list[BoundEntity]:
    """Build one BoundEntity per entity on `cap`, sharing the instance/
    key-prefix/instance-name computed once by the caller.

    `href` here is the *canonical* href discover() is iterating over;
    `subdevice.to_actual` maps it to the real, on-the-wire href the entity
    actually reads/writes (identity for MAIN, so single-subdevice devices are
    unaffected -- see subdevices.py)."""
    return [
        BoundEntity(href=subdevice.to_actual(href), capability=cap, desc=desc,
                    instance=inst,
                    key_override=f'{key_prefix}_{desc.key}' if key_prefix else None,
                    instance_name=inst_name, subdevice=subdevice)
        for desc in cap.entities
    ]


def discover(
    resources: dict[str, dict],
    registry: dict[str, list[Capability]],
    pattern_caps: Iterable[Capability] = (),
    log: Optional[Callable[[str], None]] = None,
    tier_log: Optional[Callable[[str, str], None]] = None,
    subdevice: Subdevice = MAIN,
) -> list[BoundEntity]:
    """`tier_log(href, poll_tier)` fires for every href a capability actually
    matches, even a no-entity "coverage-only" capability (see COVERAGE lists
    in capabilities/*.py) that `_bind()` turns into zero `BoundEntity` rows.
    Callers that need a href's poll cadence (the coordinator's hot/warm
    sub-poll and OBSERVE-attempt lists) must use this, not `bound` -- a
    coverage-only capability's `poll_tier` would otherwise be silently
    dropped since it never appears in `bound`.

    `resources` is always keyed by *canonical* hrefs -- for a subdevice
    (issue #177) that means its own canonical view (see
    subdevices.canonical_view), the same shape as a plain single-subdevice
    device's resources dict, so registry lookups/rt_filter/match_fn/
    instance_suffix all behave identically regardless of which subdevice is
    being discovered.
    `subdevice` only affects the *href* stamped onto each BoundEntity (via
    `_bind`, see above) and the href `log`/`tier_log` report -- both the
    real, subscribable/pollable path, not the canonical one the registry is
    keyed on.
    """
    out: list[BoundEntity] = []

    for href, rep in resources.items():
        if not isinstance(rep, dict):
            continue
        rts = rep.get('rt') or ()
        caps = registry.get(href) or []
        matched = False

        for cap in caps:
            if cap.rt_filter is not None and cap.rt_filter not in rts:
                continue
            if cap.match_fn is not None and not cap.match_fn(rep, resources):
                continue
            inst = instance_suffix(href)
            out.extend(_bind(cap, href, inst, _instance_name(cap, rep), subdevice=subdevice))
            matched = True
            if tier_log is not None:
                tier_log(subdevice.to_actual(href), cap.poll_tier)

        if matched:
            continue

        # Pattern cap fallback — first matching pattern wins
        for cap in pattern_caps:
            if cap.href_prefix and not href.startswith(cap.href_prefix):
                continue
            if cap.rt_filter is not None and cap.rt_filter not in rts:
                continue
            if cap.match_fn is not None and not cap.match_fn(rep, resources):
                continue
            inst = instance_suffix(href)
            # Auto-derive key prefix from href segments (skip digits and 'vs')
            src = href[len(cap.href_prefix):] if (cap.strip_prefix_in_key and cap.href_prefix) else href
            segs = [s for s in src.strip('/').split('/') if s and not s.isdigit() and s != 'vs']
            out.extend(_bind(cap, href, inst, _instance_name(cap, rep), '_'.join(segs),
                              subdevice=subdevice))
            matched = True
            if tier_log is not None:
                tier_log(subdevice.to_actual(href), cap.poll_tier)
            break

        if not matched and not caps and log is not None:
            log(subdevice.to_actual(href))

    return out
