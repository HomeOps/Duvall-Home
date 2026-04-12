"""
Unit tests for smart vent Jinja2 templates.

Tests the close/open logic outside of Home Assistant by mocking
the HA-specific template functions (states, state_attr, etc.).

Run with: python -m pytest packages/smartvents/test_vent_templates.py -v
Requires: pip install jinja2 pytest
"""

import jinja2
import pytest


# ---------------------------------------------------------------------------
# Mock HA state objects
# ---------------------------------------------------------------------------

class MockEntity:
    """Mimics a Home Assistant state object with entity_id and attributes."""
    def __init__(self, entity_id, state=None, attributes=None):
        self.entity_id = entity_id
        self.state = state or "open"
        self._attributes = attributes or {}

    def __getattr__(self, name):
        if name in self._attributes:
            return self._attributes[name]
        raise AttributeError(name)


class MockStates:
    """Mimics the Home Assistant `states` object in Jinja2 templates."""

    def __init__(self, entities: dict[str, MockEntity], sensor_values: dict[str, str]):
        self._entities = entities       # entity_id -> MockEntity
        self._sensor_values = sensor_values  # entity_id -> state string

    @property
    def cover(self):
        return [e for e in self._entities.values() if e.entity_id.startswith("cover.")]

    def __getitem__(self, domain):
        return [e for e in self._entities.values() if e.entity_id.startswith(f"{domain}.")]

    def __call__(self, entity_id):
        """states('sensor.xxx') -> returns the state value string."""
        if entity_id in self._sensor_values:
            return self._sensor_values[entity_id]
        if entity_id in self._entities:
            return self._entities[entity_id].state
        return "unknown"


# ---------------------------------------------------------------------------
# Template under test (close logic)
# ---------------------------------------------------------------------------

CLOSE_TEMPLATE = """\
{% macro get_name(vent) -%}
{{- vent.entity_id.replace('_vent','').replace('cover.','') -}}
{%- endmacro -%}
{% macro get_room_temp(vent) -%}
sensor.{{get_name(vent)}}_sensor_air_temperature
{%- endmacro -%}
{% macro get_close(vent) -%}
{% set target = state_attr("climate.smart_climate", "temperature")|float(20) -%}
{% set hvacMode = state_attr("climate.smart_climate", "hvac_action")|lower -%}
{% set room = states(get_room_temp(vent))|float(20) -%}
{% set level = state_attr(vent.entity_id, "current_position") -%}
{% if level == None -%}{% set level = 50 -%}{% endif -%}
{% set delta = 0.25 -%}
{% set roomIsHot = (room - target) > delta -%}
{% set roomIsCold = (target - room) > delta -%}
{% set requestClose = (roomIsCold and hvacMode == 'cooling') or (roomIsHot and hvacMode == 'heating') -%}
{% if requestClose and level > 50 -%}
{{vent.entity_id}}
{%- endif -%}
{%- endmacro -%}
{% set ns = namespace(results=[]) %}
{% for vent in states.cover if vent.entity_id.endswith('_vent') %}
  {% set result = get_close(vent) %}
  {% if result %}
    {% set ns.results = ns.results + [result] %}
  {% endif %}
{% endfor %}
{{ ns.results | join(',') }}
"""

OPEN_TEMPLATE = """\
{% macro get_name(vent) -%}
{{- vent.entity_id.replace('_vent','').replace('cover.','') -}}
{%- endmacro -%}
{% macro get_room_temp(vent) -%}
sensor.{{get_name(vent)}}_sensor_air_temperature
{%- endmacro -%}
{% macro get_open(vent) -%}
{% set target = state_attr("climate.smart_climate", "temperature")|float(20) -%}
{% set hvacMode = state_attr("climate.smart_climate", "hvac_action")|lower -%}
{% set room = states(get_room_temp(vent))|float(20) -%}
{% set level = state_attr(vent.entity_id, "current_position") -%}
{% if level == None -%}{% set level = 50 -%}{% endif -%}
{% set delta = 0.25 -%}
{% set roomIsHot = (room - target) > delta and (room != 0) -%}
{% set roomIsCold = (target - room) > delta and (room != 0) -%}
{% set requestOpen = (roomIsHot and (hvacMode == 'cooling')) or (roomIsCold and hvacMode == 'heating') -%}
{% if requestOpen and level < 50 -%}
{{vent.entity_id}}
{%- endif -%}
{%- endmacro -%}
{% set ns = namespace(results=[]) %}
{% for vent in states.cover if vent.entity_id.endswith('_vent') %}
  {% set result = get_open(vent) %}
  {% if result %}
    {% set ns.results = ns.results + [result] %}
  {% endif %}
{% endfor %}
{{ ns.results | join(',') }}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def render_template(template_str, entities, sensor_values, climate_attrs):
    """Render a Jinja2 template with mocked HA state."""
    mock_states = MockStates(entities, sensor_values)

    def state_attr(entity_id, attr):
        if entity_id in entities:
            return entities[entity_id]._attributes.get(attr)
        # Climate attrs are passed separately
        if entity_id in climate_attrs:
            return climate_attrs[entity_id].get(attr)
        return None

    env = jinja2.Environment(undefined=jinja2.Undefined)
    tpl = env.from_string(template_str)
    result = tpl.render(
        states=mock_states,
        state_attr=state_attr,
    )
    return result.strip()


def make_vent(name, position=80):
    """Create a mock vent cover entity."""
    return MockEntity(
        f"cover.{name}_vent",
        state="open" if position > 0 else "closed",
        attributes={"current_position": position},
    )


def make_scenario(vents, sensor_temps, hvac_action, target_temp):
    """
    Build the entities, sensor_values, and climate_attrs dicts
    for a test scenario.

    vents: list of (name, position) tuples
    sensor_temps: dict of name -> room temperature
    hvac_action: 'cooling' | 'heating' | 'idle'
    target_temp: float
    """
    entities = {}
    sensor_values = {}

    for name, position in vents:
        vent = make_vent(name, position)
        entities[vent.entity_id] = vent
        sensor_id = f"sensor.{name}_sensor_air_temperature"
        sensor_values[sensor_id] = str(sensor_temps.get(name, 20))

    climate_attrs = {
        "climate.smart_climate": {
            "temperature": target_temp,
            "hvac_action": hvac_action,
        }
    }

    return entities, sensor_values, climate_attrs


# ---------------------------------------------------------------------------
# Tests — Close Logic
# ---------------------------------------------------------------------------

class TestCloseTemplate:

    def test_cooling_room_is_cold_should_close(self):
        """Room below target while cooling -> vent should close."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 80)],
            sensor_temps={"kitchen": 19.0},
            hvac_action="cooling",
            target_temp=22.0,
        )
        result = render_template(CLOSE_TEMPLATE, entities, sensors, climate)
        assert "cover.kitchen_vent" in result

    def test_heating_room_is_hot_should_close(self):
        """Room above target while heating -> vent should close."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 80)],
            sensor_temps={"kitchen": 24.0},
            hvac_action="heating",
            target_temp=21.0,
        )
        result = render_template(CLOSE_TEMPLATE, entities, sensors, climate)
        assert "cover.kitchen_vent" in result

    def test_cooling_room_is_hot_should_not_close(self):
        """Room above target while cooling -> vent should stay open."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 80)],
            sensor_temps={"kitchen": 24.0},
            hvac_action="cooling",
            target_temp=21.0,
        )
        result = render_template(CLOSE_TEMPLATE, entities, sensors, climate)
        assert result == ""

    def test_heating_room_is_cold_should_not_close(self):
        """Room below target while heating -> vent should stay open."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 80)],
            sensor_temps={"kitchen": 19.0},
            hvac_action="heating",
            target_temp=22.0,
        )
        result = render_template(CLOSE_TEMPLATE, entities, sensors, climate)
        assert result == ""

    def test_vent_already_below_50_should_not_close(self):
        """Even if close is requested, vent at position <= 50 is skipped."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 30)],
            sensor_temps={"kitchen": 19.0},
            hvac_action="cooling",
            target_temp=22.0,
        )
        result = render_template(CLOSE_TEMPLATE, entities, sensors, climate)
        assert result == ""

    def test_within_delta_no_action(self):
        """Room within 0.25 delta of target -> no close."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 80)],
            sensor_temps={"kitchen": 22.1},
            hvac_action="cooling",
            target_temp=22.0,
        )
        result = render_template(CLOSE_TEMPLATE, entities, sensors, climate)
        assert result == ""

    def test_idle_hvac_no_action(self):
        """HVAC idle -> neither heating nor cooling matches, no close."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 80)],
            sensor_temps={"kitchen": 19.0},
            hvac_action="idle",
            target_temp=22.0,
        )
        result = render_template(CLOSE_TEMPLATE, entities, sensors, climate)
        assert result == ""

    def test_multiple_vents_mixed(self):
        """Multiple vents: only those meeting close criteria are listed."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 80), ("bedroom", 80), ("den", 80)],
            sensor_temps={"kitchen": 19.0, "bedroom": 24.0, "den": 22.0},
            hvac_action="cooling",
            target_temp=22.0,
        )
        result = render_template(CLOSE_TEMPLATE, entities, sensors, climate)
        # kitchen is cold while cooling -> close
        assert "cover.kitchen_vent" in result
        # bedroom is hot while cooling -> don't close (needs cooling)
        assert "cover.bedroom_vent" not in result
        # den is at target -> no action
        assert "cover.den_vent" not in result

    def test_comma_separated_output(self):
        """Multiple closed vents should be comma-separated."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 80), ("den", 80)],
            sensor_temps={"kitchen": 19.0, "den": 18.0},
            hvac_action="cooling",
            target_temp=22.0,
        )
        result = render_template(CLOSE_TEMPLATE, entities, sensors, climate)
        parts = [p.strip() for p in result.split(",")]
        assert "cover.kitchen_vent" in parts
        assert "cover.den_vent" in parts

    def test_no_vents_empty_result(self):
        """No cover entities -> empty output."""
        result = render_template(CLOSE_TEMPLATE, {}, {}, {
            "climate.smart_climate": {"temperature": 22, "hvac_action": "cooling"}
        })
        assert result == ""


# ---------------------------------------------------------------------------
# Tests — Open Logic
# ---------------------------------------------------------------------------

class TestOpenTemplate:

    def test_cooling_room_is_hot_should_open(self):
        """Room above target while cooling -> vent should open."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 20)],
            sensor_temps={"kitchen": 24.0},
            hvac_action="cooling",
            target_temp=21.0,
        )
        result = render_template(OPEN_TEMPLATE, entities, sensors, climate)
        assert "cover.kitchen_vent" in result

    def test_heating_room_is_cold_should_open(self):
        """Room below target while heating -> vent should open."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 20)],
            sensor_temps={"kitchen": 19.0},
            hvac_action="heating",
            target_temp=22.0,
        )
        result = render_template(OPEN_TEMPLATE, entities, sensors, climate)
        assert "cover.kitchen_vent" in result

    def test_cooling_room_is_cold_should_not_open(self):
        """Room below target while cooling -> don't open."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 20)],
            sensor_temps={"kitchen": 19.0},
            hvac_action="cooling",
            target_temp=22.0,
        )
        result = render_template(OPEN_TEMPLATE, entities, sensors, climate)
        assert result == ""

    def test_vent_already_above_50_should_not_open(self):
        """Vent at position >= 50 is skipped even if open is requested."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 80)],
            sensor_temps={"kitchen": 24.0},
            hvac_action="cooling",
            target_temp=21.0,
        )
        result = render_template(OPEN_TEMPLATE, entities, sensors, climate)
        assert result == ""

    def test_room_temp_zero_ignored(self):
        """Room temp of 0 (sensor unavailable) -> skip to avoid false opens."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 20)],
            sensor_temps={"kitchen": 0},
            hvac_action="cooling",
            target_temp=22.0,
        )
        result = render_template(OPEN_TEMPLATE, entities, sensors, climate)
        assert result == ""

    def test_null_position_defaults_to_50(self):
        """If current_position is None, defaults to 50 (not < 50, so no open)."""
        entities, sensors, climate = make_scenario(
            vents=[("kitchen", 80)],  # position will be overridden
            sensor_temps={"kitchen": 24.0},
            hvac_action="cooling",
            target_temp=21.0,
        )
        # Override position to None
        entities["cover.kitchen_vent"]._attributes["current_position"] = None
        result = render_template(OPEN_TEMPLATE, entities, sensors, climate)
        # level defaults to 50, which is not < 50, so no open
        assert result == ""

    def test_non_vent_covers_ignored(self):
        """Covers not ending in _vent should be skipped."""
        entities, sensors, climate = make_scenario(
            vents=[],
            sensor_temps={},
            hvac_action="cooling",
            target_temp=22.0,
        )
        # Add a non-vent cover
        entities["cover.garage_door"] = MockEntity("cover.garage_door", attributes={"current_position": 20})
        result = render_template(OPEN_TEMPLATE, entities, sensors, climate)
        assert result == ""


# ---------------------------------------------------------------------------
# Tests — get_name macro (whitespace correctness)
# ---------------------------------------------------------------------------

class TestGetNameMacro:

    MACRO_TEST = """\
{% macro get_name(vent) -%}
{{- vent.entity_id.replace('_vent','').replace('cover.','') -}}
{%- endmacro -%}
sensor.{{get_name(vent)}}_sensor_air_temperature
"""

    def test_no_leading_whitespace_in_name(self):
        """get_name should not inject whitespace into entity IDs."""
        vent = MockEntity("cover.kitchen_vent")
        env = jinja2.Environment()
        tpl = env.from_string(self.MACRO_TEST)
        result = tpl.render(vent=vent).strip()
        assert result == "sensor.kitchen_sensor_air_temperature"
        assert "\n" not in result
        assert "  " not in result
