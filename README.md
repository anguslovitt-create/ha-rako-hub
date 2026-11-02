# Rako Hub (observability) for Home Assistant

A small companion integration for Rako lighting systems that talks to the **Rako Hub's local TCP API** (port 9762) and exposes what the standard Rako integration doesn't:

- `binary_sensor.rako_hub_link` — connected to the hub (auto-reconnects with backoff; STATUS keepalive every 60 s)
- `sensor.rako_hub_last_activity` — timestamp of the last command seen on the Rako wire
- `sensor.rako_hub_<room>_scene` — one per room: current scene number, with attributes listing every channel, its live level, and its **stored level in every scene (S1–S16)** as programmed in RASOFT
- Events: `rako_hub_feedback` (every command on the wire — keypad presses, app, timers, HA) and `rako_hub_tracker` (level/scene changes) — use `rako_hub_feedback` as an automation trigger for physical keypads
- Services: `rako_hub.set_scene`, `set_level`, `fade`, `set_rgb`, `refresh`, and an **experimental** `store_scene`

It runs alongside the regular Rako integration; it creates no light entities.

## Install
HACS → Integrations → ⋮ → Custom repositories → add this repo (Integration) → install → restart → Settings → Devices & services → Add → "Rako Hub (observability)" → enter the hub IP.

## Example: keypad press as a trigger
```yaml
triggers:
  - trigger: event
    event_type: rako_hub_feedback
    event_data:
      room: GFHall
      command: SCENE
      value: 0        # the Off button
```

## Tests
`python -m pytest` — runs the client against a scripted fake hub; no Home Assistant needed.

Protocol reference: Rako "Accessing the Rako Hub" API document.
