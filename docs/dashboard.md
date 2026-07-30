# Dashboard cards

The integration brings its own sidebar panel, but you probably want the important line on the
dashboard you already look at. Everything below is copy-and-paste and needs no custom cards
unless it says so.

Replace `bedroom` / `living_south` with your own room and window names.

---

## The one-liner

The smallest thing that is genuinely useful: status, reason and countdown.

```yaml
type: markdown
content: >-
  {% set s = states('sensor.adaptive_ventilation_status') %}
  {% set a = state_attr('sensor.adaptive_ventilation_status', 'headline') %}
  {% set c = state_attr('sensor.adaptive_ventilation_status', 'countdown') %}
  ## {{ {'idle':'😴 Nothing to do',
          'ventilate_now':'🪟 Ventilate now',
          'night_flush':'🌙 Night flush',
          'keep_closed':'🔒 Keep closed',
          'heat_protection':'🌞 Heat protection',
          'purge_running':'⏱ Purge running',
          'air_quality':'💨 Air quality',
          'storm':'⛈ Storm',
          'away':'🚪 Away',
          'off':'⏸ Off',
          'unavailable_data':'⚠️ No usable data'}.get(s, s) }}

  {{ a }}
  {% if c is not none %}

  *in {{ c }} min*
  {% endif %}
```

---

## The full status card

Status, the three numbers that matter, the next tipping point and the cooling budget.

```yaml
type: vertical-stack
cards:
  - type: markdown
    content: >-
      {% set st = 'sensor.adaptive_ventilation_status' %}
      ### {{ state_attr(st, 'line1') }}

      {{ state_attr(st, 'headline') }}
  - type: glance
    show_name: true
    entities:
      - entity: sensor.adaptive_ventilation_tipping_point_morning
        name: Close at
      - entity: sensor.adaptive_ventilation_tipping_point_evening
        name: Open at
      - entity: sensor.adaptive_ventilation_cooling_budget_today
        name: Obtainable
      - entity: sensor.adaptive_ventilation_open_windows_count
        name: Open
  - type: entities
    entities:
      - entity: select.adaptive_ventilation_mode
      - entity: switch.adaptive_ventilation_notifications
      - entity: binary_sensor.adaptive_ventilation_action_required
      - type: buttons
        entities:
          - entity: button.adaptive_ventilation_purge_now
          - entity: button.adaptive_ventilation_snooze_1h
```

---

## Per-window advice

One row per window, showing what it should be doing and why.

```yaml
type: markdown
content: >-
  {% for w in states.sensor
       | selectattr('entity_id', 'search', '_recommendation$')
       | rejectattr('attributes.reason', 'none') %}
  {% set icon = {'open_wide':'🪟','open_tilt':'↗','purge':'💨',
                 'cross_ventilate':'↔','close':'🚪','keep_closed':'🔒',
                 'keep_open':'🪟','no_action':'·'}.get(w.state, '·') %}
  {{ icon }} **{{ w.name | replace(' Recommendation', '') }}** — {{ w.attributes.reason }}
  {% if w.attributes.duration_minutes %} *({{ w.attributes.duration_minutes }} min)*{% endif %}

  {% endfor %}
```

---

## Mushroom variant

Needs [Mushroom](https://github.com/piitaya/lovelace-mushroom) from HACS.

```yaml
type: vertical-stack
cards:
  - type: custom:mushroom-template-card
    primary: >-
      {{ state_attr('sensor.adaptive_ventilation_status', 'line1') }}
    secondary: >-
      {{ state_attr('sensor.adaptive_ventilation_status', 'headline') }}
    icon: >-
      {% set a = state_attr('sensor.adaptive_ventilation_status', 'action') %}
      {{ {'open_wide':'mdi:window-open','open_tilt':'mdi:window-open-variant',
          'purge':'mdi:air-filter','cross_ventilate':'mdi:swap-horizontal',
          'close':'mdi:window-closed','keep_closed':'mdi:window-closed-variant',
          'cover_down':'mdi:window-shutter','cover_up':'mdi:window-shutter-open',
          'fan_on':'mdi:fan'}.get(a, 'mdi:weather-windy') }}
    icon_color: >-
      {% set s = state_attr('sensor.adaptive_ventilation_status', 'severity') %}
      {{ {'SAFETY':'red','HEALTH':'orange','COMFORT':'green'}.get(s, 'grey') }}
    multiline_secondary: true
    tap_action:
      action: navigate
      navigation_path: /adaptive-ventilation-panel
  - type: custom:mushroom-chips-card
    chips:
      - type: entity
        entity: sensor.adaptive_ventilation_tipping_point_morning
        icon: mdi:weather-sunset-up
      - type: entity
        entity: sensor.adaptive_ventilation_cooling_budget_today
        icon: mdi:scale-balance
      - type: entity
        entity: sensor.adaptive_ventilation_open_windows_count
        icon: mdi:window-open
      - type: template
        icon: mdi:blur
        content: >-
          {{ states('sensor.bedroom_mold_risk') }}
        icon_color: >-
          {{ {'none':'green','low':'green','moderate':'orange',
              'high':'red','condensation':'red'}.get(
                 states('sensor.bedroom_mold_risk'), 'grey') }}
```

---

## Room overview

```yaml
type: entities
title: Rooms
entities:
  - entity: sensor.bedroom_air_quality_score
  - entity: sensor.bedroom_absolute_humidity
    secondary_info: last-changed
  - entity: sensor.bedroom_dew_point
  - entity: sensor.bedroom_mold_risk
  - entity: sensor.bedroom_heating_rate
  - type: attribute
    entity: sensor.bedroom_heating_rate
    attribute: projected_temperature
    name: Expected in 4 h
    suffix: " °C"
```

---

## Your own automations

The integration fires events rather than assuming what you want to do with them.

```yaml
automation:
  - alias: Speak urgent ventilation advice
    triggers:
      - trigger: event
        event_type: adaptive_ventilation_recommendation_added
    conditions:
      - condition: template
        value_template: "{{ trigger.event.data.priority in ['SAFETY', 'HEALTH'] }}"
    actions:
      - action: tts.speak
        target:
          entity_id: tts.piper
        data:
          media_player_entity_id: media_player.kitchen
          message: "{{ trigger.event.data.reason_data.room }}: {{ trigger.event.data.action }}"

  - alias: Flash the lamp when a purge finishes
    triggers:
      - trigger: event
        event_type: adaptive_ventilation_purge_finished
    actions:
      - action: light.turn_on
        target:
          entity_id: light.hallway
        data:
          flash: short
```
