"""Capabilities for the Samsung water-purifier family (TP2X_WATERPURIFIER-class,
issue #90, model TP2X_WATERPURIFIER_20K; also AILITE_DA-REF-WATERPURIFIER-class,
issue #196, model RWP70F15ANW/AILITE_WATERPURIFIER_25K).

Resources verified against the issue #90 and #196 diagnostics dumps.
"""
from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import BinarySensorDesc, NumberDesc, SelectDesc, SensorDesc, SwitchDesc
from .common import int_or_none, parse_iso_utc as _parse_iso_utc


DISPENSE = Capability(
    href='/setting/waterpurifier/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='dispense_type', field='x.com.samsung.da.desiredType',
                   icon='mdi:cup-water',
                   options_field='x.com.samsung.da.supportedTypes',
                   write_fn=lambda p, rep, href=None: (
                       ['setting', 'waterpurifier', 'vs', '0'],
                       {'x.com.samsung.da.desiredType': p})),
        # Only a handful of discrete temperatures are selectable (not a
        # continuous range) -- a select over the live-reported set, not a
        # number with invented bounds.
        #
        # Newer boards (issue #196, RWP70F15ANW) don't populate
        # supportedHotTemperatures at all -- they report a hotwaterRange
        # (min/max) and a hotwaterLevel (step count?) instead, with no
        # confirmed write contract for values off the old preset list. With
        # an empty options_field result, HA's current_option still returns
        # the live tempDesiredHotWater, which isn't in the (empty) options
        # list and renders as "unknown" -- the exact symptom reported. Gate
        # the entity off entirely when the board doesn't report a supported
        # list, rather than guess at hotwaterRange/hotwaterLevel's meaning.
        SelectDesc(key='hot_water_temperature', field='x.com.samsung.da.tempDesiredHotWater',
                   icon='mdi:thermometer',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedHotTemperatures',
                   exists_fn=lambda rep, resources: (
                       is_stub_rep(rep)
                       or 'x.com.samsung.da.supportedHotTemperatures' in rep),
                   write_fn=lambda p, rep, href=None: (
                       ['setting', 'waterpurifier', 'vs', '0'],
                       {'x.com.samsung.da.tempDesiredHotWater': p})),
        # Bounds and step come live from the device's own
        # desiredCapacityRange/capacityResolution fields, not a hardcoded
        # constant -- see the adding-device-support skill's "never hard-code
        # the one dump's values" section. No unit is set: capacityUnit reads
        # "C" on this dump, which can't be right for a volume field, so per
        # the 'don't guess' rule the unit is left unset rather than assumed
        # to be mL.
        NumberDesc(key='dispense_capacity', field='x.com.samsung.da.desiredCapacity',
                   icon='mdi:cup-water',
                   value_fn=int_or_none,
                   range_field='x.com.samsung.da.desiredCapacityRange',
                   step_fn=lambda rep: int_or_none(
                       rep.get('x.com.samsung.da.capacityResolution')) or 1,
                   write_fn=lambda p, rep, href=None: (
                       ['setting', 'waterpurifier', 'vs', '0'],
                       {'x.com.samsung.da.desiredCapacity': str(int(round(float(p))))})),
        BinarySensorDesc(key='pouring', field='x.com.samsung.da.pourStatus',
                          icon='mdi:cup-water',
                          value_fn=lambda v: v == 'On'),
    ),
)

STATUS = Capability(
    href='/status/waterpurifier/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='waterpurifier_status', field='x.com.samsung.da.status',
                   icon='mdi:water-pump',
                   entity_category='diagnostic'),
        BinarySensorDesc(key='filter_door_status', field='x.com.samsung.da.filterDoorStatus',
                          device_class='door',
                          entity_category='diagnostic',
                          value_fn=lambda v: v == 'Open'),
        SensorDesc(key='sterilize_period', field='x.com.samsung.da.sterilizePeriod',
                   icon='mdi:calendar-sync',
                   entity_category='diagnostic'),
        SensorDesc(key='sterilize_run_time', field='x.com.samsung.da.sterilizeRunTime',
                   icon='mdi:timer-outline',
                   entity_category='diagnostic'),
        SensorDesc(key='sterilize_last_time', device_class='timestamp',
                   entity_category='diagnostic',
                   rep_fn=lambda rep: _parse_iso_utc(
                       rep.get('x.com.samsung.da.sterilizeLastTime'))),
        SensorDesc(key='sterilize_plan_time', device_class='timestamp',
                   entity_category='diagnostic',
                   rep_fn=lambda rep: _parse_iso_utc(
                       rep.get('x.com.samsung.da.sterilizePlanTime'))),
        SensorDesc(key='filter_clean_remain_time', field='x.com.samsung.da.filterCleanRemainTime',
                   icon='mdi:timer-sand',
                   entity_category='diagnostic'),
    ),
)

FAVORITE_CAPACITY = Capability(
    href='/favorite/capacity/vs/0',
    poll_tier='cold',
    entities=(
        SwitchDesc(key='favorite_capacity_enabled', field='x.com.samsung.da.switchCapacity',
                   icon='mdi:star-outline',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['favorite', 'capacity', 'vs', '0'],
                       {'x.com.samsung.da.switchCapacity': 'On' if p == 'On' else 'Off'})),
        SelectDesc(key='favorite_capacity', field='x.com.samsung.da.defaultCapacity',
                   icon='mdi:cup-water',
                   entity_category='config',
                   options_field='x.com.samsung.da.capacityList',
                   write_fn=lambda p, rep, href=None: (
                       ['favorite', 'capacity', 'vs', '0'],
                       {'x.com.samsung.da.defaultCapacity': p})),
    ),
)

# Coffee-capable variant (issue #107) -- a "favorite" supported-list select
# for the hot water dispensed alongside brewing, same shape as
# FAVORITE_CAPACITY above.


def _status_lock_definitely_lacks_hotwater_field(resources: dict) -> bool:
    """Three-way read of /status/lock/vs/0's hotwaterLock field, favouring
    LOCK.hotwater_lock (the primary descriptor) whenever the outcome is
    still ambiguous:

    - href entirely absent from this device -> definitely no clash, the
      switchHotwater fallback below may claim the entity.
    - href present but an unfetched stub ({}) -> outcome pending, *not* a
      confirmed absence. LOCK's own exists_fn optimistically includes itself
      through a stub (matching entity.py's default), so returning True here
      too would register both descriptors -- as SwitchDescs sharing one key,
      with identical unique_ids -- until the next poll resolves it.
    - href present and fetched -> the real answer."""
    rep = resources.get('/status/lock/vs/0')
    if rep is None:
        return True
    if not rep:
        return False
    return 'x.com.samsung.da.hotwaterLock' not in rep


FAVORITE_HOTWATER = Capability(
    href='/favorite/hotwater/vs/0',
    poll_tier='cold',
    entities=(
        # Despite the resource/field naming, switchHotwater's value domain is
        # Locked/Unlocked, not an enable flag (issue #144) -- it's the same
        # hot-water lock as LOCK.hotwater_lock below, just surfaced through
        # this href on boards that don't populate /status/lock/vs/0's
        # hotwaterLock field. Shares that descriptor's key so only one "Hot
        # water lock" entity ever appears.
        #
        # Both halves of this fallback pair need an exists_fn, not just this
        # one: adapter.flatten() (the coordinator.data source every entity's
        # is_on reads) only ever honours exists_fn, never entity.py's
        # implicit "require own field present" default that gates plain
        # registration. Two same-keyed descriptors with only one of them
        # gated still both land in flatten()'s output dict -- whichever is
        # processed last silently wins, decided by device-reported href
        # order, not by which one is actually correct. So this exists_fn
        # also re-asserts its own field's presence (switchHotwater), the
        # gate a bare `field=` used to get for free before it had to share a
        # key with LOCK's descriptor.
        SwitchDesc(key='hotwater_lock', field='x.com.samsung.da.switchHotwater',
                   device_class='lock',
                   entity_category='config',
                   value_fn=lambda v: v != 'Unlocked',
                   exists_fn=lambda rep, resources: (
                       'x.com.samsung.da.switchHotwater' in rep
                       and _status_lock_definitely_lacks_hotwater_field(resources)),
                   write_fn=lambda p, rep, href=None: (
                       ['favorite', 'hotwater', 'vs', '0'],
                       {'x.com.samsung.da.switchHotwater': 'Locked' if p == 'On' else 'Unlocked'})),
        SelectDesc(key='favorite_hotwater_temperature',
                   field='x.com.samsung.da.favorite.defaultTemperature',
                   icon='mdi:thermometer',
                   entity_category='config',
                   options_field='x.com.samsung.da.favorite.supportedList',
                   write_fn=lambda p, rep, href=None: (
                       ['favorite', 'hotwater', 'vs', '0'],
                       {'x.com.samsung.da.favorite.defaultTemperature': p})),
    ),
)

# Coffee-capable variant (issue #107). No 'x.com.samsung.da.' field prefix
# on this resource, unlike the rest of the water-purifier surface.
COFFEE = Capability(
    href='/favorite/coffee/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='favorite_coffee_enabled', field='favorite.activate',
                   icon='mdi:coffee-outline',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['favorite', 'coffee', 'vs', '0'],
                       {'favorite.activate': 'On' if p == 'On' else 'Off'})),
        SensorDesc(key='coffee_brew_status', field='brew.status',
                   icon='mdi:coffee-outline',
                   entity_category='diagnostic'),
    ),
)

# Cup-detection status (issue #196, RWP70F15ANW). Only "UnReady" observed;
# the full state domain isn't confirmed, so this stays a plain diagnostic
# sensor rather than an enum with an invented state table.
CUP_STATE = Capability(
    href='/cup/state/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='cup_state', field='water.cup.state',
                   icon='mdi:cup-outline', entity_category='diagnostic'),
    ),
)

# Sound mode/output/volume (issue #196). Shapes echo laundry.py/
# air_purifier.py's same-named hrefs, but this board's own values differ
# from both (supportedModes here is voice/fixedTone/mute, not laundry's
# voice/tone/mute nor air_purifier's mute/buzzer) -- reusing either would
# reject a live-supported value, so these read the device's own supported
# list/range like air_purifier's versions do.
SOUND_MODE = Capability(
    href='/settings/sound/mode/vs/0',
    poll_tier='cold',
    entities=(
        SelectDesc(key='sound_mode', translation_key='water_purifier_sound_mode',
                   field='mode',
                   icon='mdi:volume-high',
                   entity_category='config',
                   options_field='supportedModes',
                   write_fn=lambda p, rep, href=None: (
                       ['settings', 'sound', 'mode', 'vs', '0'], {'mode': p})),
    ),
)

SOUND_OUTPUT = Capability(
    href='/settings/sound/output/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='sound_output', field='deviceType',
                   icon='mdi:volume-high', entity_category='diagnostic'),
        # No confirmed write contract (no sibling field advertising this as
        # user-settable) -- surfaced read-only per the 'don't guess' rule.
        BinarySensorDesc(key='alarm_in_mute', field='alarmInMute',
                          icon='mdi:volume-mute',
                          entity_category='diagnostic',
                          value_fn=lambda v: str(v).lower() == 'true'),
    ),
)

SOUND_VOLUME = Capability(
    href='/settings/sound/volume/vs/0',
    poll_tier='cold',
    entities=(
        NumberDesc(key='sound_volume', field='level',
                   icon='mdi:volume-medium',
                   entity_category='config',
                   native_min_fn=lambda rep: int_or_none(rep.get('minLevel')) or 0,
                   native_max_fn=lambda rep: int_or_none(rep.get('maxLevel')) or 0,
                   step_fn=lambda rep: int_or_none(rep.get('resolution')) or 1,
                   value_fn=int_or_none,
                   write_fn=lambda p, rep, href=None: (
                       ['settings', 'sound', 'volume', 'vs', '0'],
                       {'level': str(int(p))})),
    ),
)

# Last-pour statistics (issue #196). last.capacity's unit isn't confirmed
# (no sibling unit field on this resource, unlike DISPENSE.dispense_capacity
# which at least has an -- albeit suspect -- capacityUnit) so it's left
# unitless rather than assumed to be mL.
STATISTIC_POUR = Capability(
    href='/statistic/pour/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='last_pour_type', field='last.type',
                   icon='mdi:cup-water', entity_category='diagnostic'),
        SensorDesc(key='last_pour_capacity', field='last.capacity',
                   icon='mdi:cup-water', entity_category='diagnostic',
                   value_fn=int_or_none),
    ),
)

LOCK = Capability(
    href='/status/lock/vs/0',
    poll_tier='warm',
    entities=(
        # Shares its key with FAVORITE_HOTWATER's switchHotwater fallback
        # above (issue #144); see the comment there for why this half also
        # needs an explicit exists_fn now that the two share a key in
        # adapter.flatten()'s output. A stub rep ({}) still counts as
        # "present" here (matches entity.py's own default for a field-less
        # gate) since the alternative -- treating an unfetched resource as
        # confirmed-absent -- is what let both descriptors register at once.
        SwitchDesc(key='hotwater_lock', field='x.com.samsung.da.hotwaterLock',
                   device_class='lock',
                   entity_category='config',
                   value_fn=lambda v: v != 'Unlocked',
                   exists_fn=lambda rep, resources: (
                       not rep or 'x.com.samsung.da.hotwaterLock' in rep),
                   write_fn=lambda p, rep, href=None: (
                       ['status', 'lock', 'vs', '0'],
                       {'x.com.samsung.da.hotwaterLock': 'Locked' if p == 'On' else 'Unlocked'})),
        SwitchDesc(key='coldwater_lock', field='x.com.samsung.da.coldwaterLock',
                   device_class='lock',
                   entity_category='config',
                   value_fn=lambda v: v != 'Unlocked',
                   write_fn=lambda p, rep, href=None: (
                       ['status', 'lock', 'vs', '0'],
                       {'x.com.samsung.da.coldwaterLock': 'Locked' if p == 'On' else 'Unlocked'})),
        SwitchDesc(key='buzz_lock', field='x.com.samsung.da.buzzLock',
                   device_class='lock',
                   entity_category='config',
                   value_fn=lambda v: v != 'Unlocked',
                   write_fn=lambda p, rep, href=None: (
                       ['status', 'lock', 'vs', '0'],
                       {'x.com.samsung.da.buzzLock': 'Locked' if p == 'On' else 'Unlocked'})),
    ),
)

# ---------------------------------------------------------------------------
# Water-purifier-scoped coverage: hrefs with no user-actionable state or no
# confirmed contract, following the 'don't guess' rule.
# ---------------------------------------------------------------------------
_WP_IGNORED = [
    # supportedModes carries a single opaque wizard-workflow token
    # ('HOMECARE_WIZARD_V2') and modes reports a completely different,
    # unrelated value ('WATERFILTER_DISABLE') not even present in
    # supportedModes -- internal plumbing, not a real user-facing mode
    # select. OCF-standard /mode/0 mirrors the same vendor resource but is
    # already covered by the global ignored.IGNORED (fridge's OCF-native
    # vacation-mode flag shares that href).
    '/mode/vs/0',
    # Static support-flags blob (automation.supported.modes/options) -- no
    # live "current automation setting" field to expose.
    '/automation/waterpurifier/vs/0',
    # Coffee-capable variant (issue #107). All four are static
    # capability-advertisement blobs or empty -- no live "current recipe" /
    # "current custom slot" field to expose, unlike /favorite/coffee/vs/0
    # (COFFEE above), which does carry live brew status.
    '/brand/recipe/info/vs/0',        # revision + max-brand-count metadata
    '/coffee/custom/recipe/vs/0',     # publisher.support: allowed custom-recipe slot IDs
    '/recipe/coffee/vs/0',            # same publisher.support shape, no per-recipe content
    '/recipe/coffee/deletion/vs/0',   # empty {} on this dump
]

COVERAGE = [Capability(href=h) for h in _WP_IGNORED]
