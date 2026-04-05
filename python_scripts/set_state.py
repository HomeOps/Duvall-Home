entity_id = data.get("entity_id")
new_state = data.get("state")
extra = data.get("attributes", {})

# Get current attributes
current = hass.states.get(entity_id)
attributes = {}

# Copy existing attributes
for key in current.attributes:
    attributes[key] = current.attributes[key]

# Merge user-supplied attributes
for key in extra:
    attributes[key] = extra[key]

if current is not None and current.state == new_state:
    temp_state = "on" if new_state == "off" else "off"
    hass.states.set(entity_id, temp_state, attributes)

# Write state back with merged attributes
hass.states.set(entity_id, new_state, attributes)

