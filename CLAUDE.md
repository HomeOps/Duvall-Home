# Duvall Home - Home Assistant Configuration

## Overview
This is a Home Assistant configuration repository for a smart home setup. It includes custom components, automations, and integrations for climate control, smart vents, and UI enhancements.

## Key Components

### Custom Components
- **smart_climate**: EcoBee-like smart thermostat wrapper that manages comfort presets (Home, Sleep, Away) with automatic HEAT/COOL switching based on inside/outside temperature sensors
  - Uses hysteresis logic to prevent rapid mode cycling
  - Supports both AUTO mode (temperature ranges) and manual modes (single setpoint)

### Configurations
- **esphome/**: Device firmware configurations (e.g., Midea heat pump)
- **packages/smartvents/**: Smart vent automations and controls
- **www/community/**: Custom frontend components (kiosk mode for wall displays)
- **python_scripts/**: Utility scripts for state management

## Development Guidelines

### Making Changes
- Test automations and climate logic changes against Home Assistant's climate entity specifications
- The smart_climate component relies on state synchronization with underlying climate devices—verify no feedback loops occur
- When modifying kiosk-mode.js, maintain both standard and ES5 versions for browser compatibility

### Git Workflow
- Main branch: `master`
- Commit messages should describe the "why" and "what"
- Include any breaking changes or new dependencies clearly

### No Special Requirements
- This is a standard git workflow repository
- No CI/CD pipelines or protected branch rules in place
- Commits go directly to master

## File Structure
```
├── custom_components/    # Home Assistant custom integrations
├── esphome/             # ESPHome device configs
├── packages/            # Automation packages (included in configuration.yaml)
├── python_scripts/      # Helper scripts
└── www/community/       # Custom frontend components
```

## Testing Recommendations
- Test climate entity mode transitions and preset changes
- Verify sensor updates trigger appropriate automation responses
- Check that external changes to underlying devices don't cause feedback loops
