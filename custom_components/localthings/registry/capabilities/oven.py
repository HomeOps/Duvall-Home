"""Capabilities for the oven family (Samsung NV7000BS-class).

Resources verified against the live device via DTLS-CoAP.
See `local-tools/comparisons/oven-tree.md` for the full field reference.

Write surfaces this module exposes:

  proven:
    * Lamp via /mode/vs/0 options RMW (probe_oven_lamp_toggle.py)
      — works even with Remote Control off.

  unproven (first HA use is also the test):
    * Sound, FastPreheat, NaturalSteam — same RMW pattern as lamp.
    * Setpoint via /temperatures/vs/0 items RMW.
    * Cook time via /operational/state/vs/0 operationTime/remainingTime.
    * Mode select via /mode/vs/0 .modes — mid-cook acceptance unknown.
    * Stop via /operational/state/vs/0 state='Ready'.

Note: Cycle start is not implemented. Reverse-engineering shows local-OCF
cycle start is not reproducible on this firmware (see project_oven_remote
_start_open.md). Mode writes are also unreliable — the oven rolls them back
once a cycle is active. OVEN_MODE is provided as a SelectDesc for fidelity
but is effectively read-only in practice.
"""
from datetime import datetime, timezone, timedelta

from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import (
    BinarySensorDesc, NumberDesc, SelectDesc, SensorDesc, SwitchDesc,
)
from .common import normalize_temp_unit
from .operational import STOP_BUTTON

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SETPOINT_MIN_C = 30
SETPOINT_MAX_C = 270
SETPOINT_STEP_C = 5

# Verified against issue #44's range dump (NSI6DG9100SRAA, unit reported as
# "Fahrenheit" on /temperatures/vs/0): Bake mode's modeSpec on /mode/vs/0
# reports tempMinF/tempMaxF/tempIntervalF = 175/550/5. Kept as a separate
# constant set rather than converted from the Celsius bounds above, which
# are themselves unverified (no live dump; see module docstring).
SETPOINT_MIN_F = 175
SETPOINT_MAX_F = 550
SETPOINT_STEP_F = 5

# Mode options seen on NV7000BS-class. No dump exists so this list is inferred
# from Samsung documentation and firmware observations. The firmware will
# reject unknown modes; missing entries here are a coverage gap, not a bug.
#
# This is a fallback only, used when a device's own /mode/vs/0 doesn't report
# x.com.samsung.da.supportedModes at all -- see _oven_mode_options/
# _oven_mode_write below. issue #138's range dump (NE63A6511SS/AA) reports
# ConvectionRoast/KeepWarm/BreadProof/AirFryer/Dehydrate/SelfClean/SteamClean
# in its own supportedModes; those are read live rather than added here, per
# the adding-device-support skill's preference for device-reported option
# lists over hardcoded ones.
_OVEN_MODES = (
    'NoOperation',
    'Bake',
    'Broil',
    'Convection',
    'ConvectionBake',
    'ConvectionBroil',
    'FrozenPizzaPlus',
    'SlowCook',
    'PlateWarm',
    'AirFry',
)

_SAMSUNG_STATE_TO_OCF = {
    'Ready':   'idle',
    'Run':     'active',
    'Running': 'active',
    'Pause':   'pause',
    'Paused':  'pause',
    'End':     'idle',
    'Stop':    'idle',
}


def _to_ocf(v):
    return _SAMSUNG_STATE_TO_OCF.get(v, v) if v is not None else None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _finish_time(remaining_str):
    if not remaining_str:
        return None
    try:
        h, m, s = remaining_str.split(':')
        total_s = int(h) * 3600 + int(m) * 60 + int(s)
        if total_s == 0:
            return None
        return datetime.now(timezone.utc) + timedelta(seconds=total_s)
    except Exception:
        return None


def _op_minutes(op_time):
    """Parse 'H:MM:SS' operationTime into integer minutes."""
    if not op_time:
        return None
    try:
        h, m, s = op_time.split(':')
        return int(h) * 60 + int(m) + (1 if int(s) > 0 else 0)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Options-array helpers (shared by lamp, sound, fastpreheat, naturalsteam)
# ---------------------------------------------------------------------------

def _option_value(options, prefix):
    """Find `<prefix>_<value>` in an options array and return <value>."""
    for o in (options or []):
        if isinstance(o, str) and o.startswith(prefix + '_'):
            return o.split('_', 1)[1]
    return None


def _has_option(prefix):
    """exists_fn for an options-array switch: bind only when the device's own
    options[] actually carries a `<prefix>_<value>` token.

    fast_preheat/natural_steam were shipped unconditionally (no exists_fn) as
    an unverified guess (see module docstring) -- issue #183's dump (model
    NE6516A) reports neither `fastpreheat_*` nor `NaturalSteam_*` in its
    options[] at all, so both switches were phantom controls: always read as
    off, and toggling them wrote a token the firmware never recognized in
    the first place, hence "does not appear to do anything."

    `is_stub_rep(rep) or` keeps the same stub carve-out as cooktop.py's
    identical per-token exists_fn on its own options[]-array href: a stub
    /device/0 seed rep (not yet sub-polled) has no options[] at all, and
    without this an entity whose token is genuinely present would never get
    a first chance to bind, since exists_fn runs before that first real
    fetch lands.
    """
    return lambda rep, resources: is_stub_rep(rep) or _option_value(
        rep.get('x.com.samsung.da.options'), prefix) is not None


def _option_write(prefix, new_value):
    """A one-token x.com.samsung.da.options write, mirroring
    laundry.option_write. NOT independently confirmed on an oven -- issue
    #54 only confirmed prefix-merge-on-write for a washer's /course/vs/0.
    This extrapolates that same vendor field/contract to the oven's
    /mode/vs/0, on the assumption the firmware handles the array the same
    way there. If that assumption is wrong for some oven, a device that
    replaces the field outright instead of merging would drop every other
    option in it (Sound/fastpreheat/etc.) on the next write -- revisit if a
    real device report surfaces that."""
    return [f'{prefix}_{new_value}']


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------

def _oven_setpoint_write(p, rep, href=None):
    """RMW write to /temperatures/vs/0 items array."""
    try:
        temp = float(p)
    except (TypeError, ValueError):
        return None
    min_v, max_v, step_v = _setpoint_bounds(rep)
    temp_i = int(round(temp / step_v) * step_v)
    if not (min_v <= temp_i <= max_v):
        return None
    items = rep.get('x.com.samsung.da.items')
    if not items:
        return None
    items = [dict(it) for it in items]
    items[0]['x.com.samsung.da.desired'] = str(temp_i)
    return ['temperatures', 'vs', '0'], {'x.com.samsung.da.items': items}


def _cook_time_write(p, rep, href=None):
    """Write operationTime + remainingTime (H:MM:SS) from minutes."""
    try:
        minutes = int(round(float(p)))
    except (TypeError, ValueError):
        return None
    if not (0 <= minutes <= 1439):
        return None
    h, m = divmod(minutes, 60)
    hms = f"{h:02d}:{m:02d}:00"
    return ['operational', 'state', 'vs', '0'], {
        'x.com.samsung.da.operationTime': hms,
        'x.com.samsung.da.remainingTime': hms,
    }


def _oven_mode_options(resources):
    """Live mode list from the device's own /mode/vs/0 supportedModes when
    it reports one; the NV7000BS-era _OVEN_MODES guess otherwise. Mirrors
    laundry.py's options_field pattern (buzzer_sound/finish_sound), but
    needs the callable form rather than options_field because a static
    fallback has to kick in when the device's own field is absent."""
    rep = resources.get('/mode/vs/0') or {}
    live = rep.get('x.com.samsung.da.supportedModes')
    return list(live) if live else list(_OVEN_MODES)


def _oven_mode_write(p, rep, href=None):
    valid = rep.get('x.com.samsung.da.supportedModes') or _OVEN_MODES
    if p not in valid:
        return None
    return ['mode', 'vs', '0'], {'x.com.samsung.da.modes': [p]}


def _option_switch_write(prefix):
    """Factory for a single-token on/off options-array write -- lamp, sound,
    fast_preheat, natural_steam, energy_saving, and cooktop_on_alert were all
    a byte-for-byte copy of this same shape, one per prefix."""
    def write(p, rep, href=None):
        if p not in ('On', 'Off'):
            return None
        if not rep.get('x.com.samsung.da.options'):
            return None
        return ['mode', 'vs', '0'], {
            'x.com.samsung.da.options': _option_write(prefix, p),
        }
    return write


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

OVEN_OPERATIONAL_STATE = Capability(
    href='/operational/state/vs/0',
    poll_tier='hot',
    entities=(
        SensorDesc(key='machine_state', field='x.com.samsung.da.state',
                   icon='mdi:stove',
                   device_class='enum', options=('idle', 'active', 'pause'),
                   translation_key='machine_state', value_fn=_to_ocf),
        BinarySensorDesc(key='cycle_active', field='x.com.samsung.da.state',
                         device_class='running',
                         value_fn=lambda v: _SAMSUNG_STATE_TO_OCF.get(v) == 'active'),
        SensorDesc(key='progress_percentage',
                   field='x.com.samsung.da.progressPercentage',
                   unit='%', state_class='measurement',
                   value_fn=_int),
        SensorDesc(key='operation_time_minutes',
                   field='x.com.samsung.da.operationTime',
                   unit='min',
                   state_class='measurement', value_fn=_op_minutes),
        SensorDesc(key='finish_time', field='x.com.samsung.da.remainingTime',
                   device_class='timestamp',
                   value_fn=_finish_time),
        NumberDesc(key='cook_time', field='x.com.samsung.da.operationTime',
                   unit='min', native_min=0, native_max=1439,
                   step=1.0, icon='mdi:timer', value_fn=_op_minutes,
                   write_fn=_cook_time_write),
        STOP_BUTTON,
    ),
)

OVEN_CAVITY = Capability(
    href='/oven/vs/0',
    poll_tier='hot',
    entities=(
        SensorDesc(key='oven_state', field='x.com.samsung.da.state',
                   ),
    ),
)

def _oven_temp_unit(rep):
    """Same shape/risk as fridge.py's TEMPERATURES_FALLBACK: this is the
    same aggregate `/temperatures/vs/0` items[] resource type, which on
    fridge hardware carries a per-item `x.com.samsung.da.unit` field
    ('Celsius'/'Fahrenheit') that was previously hardcoded away (issue #7).
    Keeps the verified '°C' default when the field is absent (the original
    NV7000BS-class dump this module was written against), but reads it live
    -- issue #44's range dump is the first to report 'Fahrenheit' here."""
    items = rep.get('x.com.samsung.da.items') or []
    unit = items[0].get('x.com.samsung.da.unit') if items else None
    return normalize_temp_unit(unit, default='°C')


def _setpoint_bounds(rep):
    """(min, max, step) for the live unit -- see the SETPOINT_*_C/_F
    constants above for provenance. Bounds must track the unit shown by
    unit_fn (both read the same live rep), or the HA slider's range would
    silently mismatch its own displayed unit."""
    if _oven_temp_unit(rep) == '°F':
        return SETPOINT_MIN_F, SETPOINT_MAX_F, SETPOINT_STEP_F
    return SETPOINT_MIN_C, SETPOINT_MAX_C, SETPOINT_STEP_C


OVEN_SETPOINT = Capability(
    href='/temperatures/vs/0',
    poll_tier='hot',
    entities=(
        # NumberDesc first — test_oven_setpoint_write_is_read_modify_write uses entities[0]
        NumberDesc(key='oven_setpoint', field='x.com.samsung.da.items',
                   device_class='temperature', unit_fn=_oven_temp_unit,
                   native_min=float(SETPOINT_MIN_C), native_max=float(SETPOINT_MAX_C),
                   step=float(SETPOINT_STEP_C), icon='mdi:thermometer-chevron-up',
                   native_min_fn=lambda rep: float(_setpoint_bounds(rep)[0]),
                   native_max_fn=lambda rep: float(_setpoint_bounds(rep)[1]),
                   step_fn=lambda rep: float(_setpoint_bounds(rep)[2]),
                   value_fn=lambda items: _int(
                       (items[0].get('x.com.samsung.da.desired') if items else None)),
                   write_fn=_oven_setpoint_write),
        SensorDesc(key='current_temp_c', field='x.com.samsung.da.items',
                   device_class='temperature',
                   state_class='measurement', unit_fn=_oven_temp_unit,
                   value_fn=lambda items: _int(
                       (items[0].get('x.com.samsung.da.current') if items else None))),
    ),
)

OVEN_DOOR = Capability(
    href='/doors/vs/0',
    poll_tier='hot',
    entities=(
        BinarySensorDesc(key='door_open', field='x.com.samsung.da.items',
                         device_class='door',
                         value_fn=lambda items: (
                             items[0].get('x.com.samsung.da.openState') == 'Open'
                             if items else None)),
    ),
)

OVEN_CONNECTED = Capability(
    href='/connected/vs/0',
    poll_tier='warm',
    entities=(
        BinarySensorDesc(key='cloud_connected', field='x.com.samsung.da.connected',
                         device_class='connectivity',
                         entity_category='diagnostic',
                         value_fn=lambda v: v == 'On'),
    ),
)

# Static cavity capability metadata (count/type/supported features) -- no
# per-cavity data varies at runtime on any dump seen so far (issue #44's
# range: single cavity, no supportedFeatureList entries). Bound with no
# entities purely for coverage; revisit if a multi-cavity dump surfaces
# fields worth exposing.
OVEN_SPEC = Capability(href='/oven/spec/vs/0')

# Quick-recipe display blob (combi microwave, issue #121) -- a JSON-encoded
# string (language/menu/servingSize/option) with every field blank on the
# only dump seen, and no documented write contract. No entity to bind per
# the 'don't guess' rule; a bare Capability still marks the href covered.
OVEN_RECIPE_COOK = Capability(href='/recipe/cook/vs/0')

OVEN_MODE = Capability(
    href='/mode/vs/0',
    poll_tier='warm',
    entities=(
        # SelectDesc first — test_oven_mode_options_nonempty uses entities[0]
        SelectDesc(key='oven_mode', field='x.com.samsung.da.modes',
                   icon='mdi:tune',
                   options=_oven_mode_options,
                   value_fn=lambda v: v[0] if v else None,
                   write_fn=_oven_mode_write),
        SwitchDesc(key='lamp', field='x.com.samsung.da.options',
                   icon='mdi:track-light',
                   value_fn=lambda opts: _option_value(opts, 'UpperLamp') == 'On',
                   write_fn=_option_switch_write('UpperLamp')),
        SwitchDesc(key='sound', field='x.com.samsung.da.options',
                   icon='mdi:volume-high',
                   entity_category='config',
                   value_fn=lambda opts: _option_value(opts, 'Sound') == 'On',
                   write_fn=_option_switch_write('Sound')),
        SwitchDesc(key='fast_preheat', field='x.com.samsung.da.options',
                   icon='mdi:fire',
                   exists_fn=_has_option('fastpreheat'),
                   value_fn=lambda opts: _option_value(opts, 'fastpreheat') == 'On',
                   write_fn=_option_switch_write('fastpreheat')),
        SwitchDesc(key='natural_steam', field='x.com.samsung.da.options',
                   icon='mdi:kettle-steam',
                   exists_fn=_has_option('NaturalSteam'),
                   value_fn=lambda opts: _option_value(opts, 'NaturalSteam') == 'On',
                   write_fn=_option_switch_write('NaturalSteam')),
        # 120-hour energy-saving standby (issue #183): confirmed present in
        # this unit's options[] (EnergySaving_On) and directly requested --
        # unlike fast_preheat/natural_steam above, this token is real on this
        # hardware, just previously unbound entirely.
        SwitchDesc(key='energy_saving', field='x.com.samsung.da.options',
                   icon='mdi:leaf', entity_category='config',
                   exists_fn=_has_option('EnergySaving'),
                   value_fn=lambda opts: _option_value(opts, 'EnergySaving') == 'On',
                   write_fn=_option_switch_write('EnergySaving')),
        # Cooktop-on alert (issue #183): also confirmed present
        # (BurnerOnAlert_Off) though the reporter noted it mainly matters for
        # the SmartThings app's own alerting, not local automation.
        SwitchDesc(key='cooktop_on_alert', field='x.com.samsung.da.options',
                   icon='mdi:alert-circle-outline', entity_category='config',
                   exists_fn=_has_option('BurnerOnAlert'),
                   value_fn=lambda opts: _option_value(opts, 'BurnerOnAlert') == 'On',
                   write_fn=_option_switch_write('BurnerOnAlert')),
    ),
)
