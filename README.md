<div align="center">

<img src="custom_components/adaptive_ventilation/brand/icon.png" width="120" alt="Adaptive Ventilation">

# Adaptive Ventilation

**A Home Assistant integration that tells you when to open which window, and when to close it again.**

[![CI](https://github.com/ProfessorQuantumUniverse/Adaptive-Ventilation/actions/workflows/ci.yaml/badge.svg)](https://github.com/ProfessorQuantumUniverse/Adaptive-Ventilation/actions/workflows/ci.yaml)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.7%2B-41BDF5.svg?logo=homeassistant&logoColor=white)](https://www.home-assistant.io)
[![Licence: GPL-3.0](https://img.shields.io/badge/licence-GPL--3.0-blue.svg)](LICENSE)
<!-- [![hacs](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz) NOT NOW -->

</div>

---

## What it does

A flat without air conditioning is a thermal battery you charge by accident. You can win 3-5 K a
night, if you open the right windows at the right time and, above all, **close them before you
lose it again.** The morning crossover is the one you sleep through.

Adaptive Ventilation does that thinking for you. It watches four things, and each of them can
*demand* ventilation or *forbid* it:

|  | Axis | The question it answers |
|---|---|---|
| 🌡️ | **Temperature** | Is it actually cooler outside, in enthalpy rather than just on the thermometer? |
| 💧 | **Humidity** | Does opening the window *dry* the room, or wet it? |
| 🫁 | **CO₂** | How many minutes of purging does this room need right now? |
| 🌫️ | **Particulates** | Is the air outside worse than the air inside? |

What you get out of it:

- **`sensor.adaptive_ventilation_tipping_point_morning`** - when the outdoor temperature overtakes
  the indoor one. In summer this is *the* number, and no standard dashboard has it. You are warned
  before it, not after.
- **A 24 h plan** - "best window tonight: 23:00-05:30, expected −3.2 K."
- **Predictive shading** - the blind goes down *before* the sun reaches the window.
- **A reason for every recommendation**, with the numbers behind it.
- **Its own sidebar panel**: Now, Rooms, Tuning, Balance, with an SVG timeline.

Nothing is switched without your permission. Shutters move only where you explicitly allow it, and
windows are never motorised.

> 📖 **How and why it works** - absolute humidity, enthalpy, the RC building model,
> self-calibration, the rule catalogue and worked examples:
> **[docs/how-it-works.md](docs/how-it-works.md)**

---

## Installation

### With HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ProfessorQuantumUniverse&repository=Adaptive-Ventilation&category=integration)

Click the button, then **Download**. It adds this repository to HACS and opens it in one step.

<details>
<summary>Or add the custom repository by hand</summary>

1. Open **HACS** in the sidebar
2. **⋮** (top right) → **Custom repositories**
3. Repository: `https://github.com/ProfessorQuantumUniverse/Adaptive-Ventilation`
4. Type: **Integration** → **Add**
5. Find *Adaptive Ventilation* in the list → **Download**

</details>

### Without HACS

Copy the folder `custom_components/adaptive_ventilation` into your `config/custom_components/`.

### Then

**Restart Home Assistant** and add the integration:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=adaptive_ventilation)

Or manually: **Settings → Devices & Services → Add Integration → Adaptive Ventilation**.

---

## Setup in two minutes

The first dialog asks four things, three of them optional:

1. **A weather entity with an hourly forecast.** The one thing worth getting right, without it
   there are no tipping points and no 24 h plan.
2. An **outdoor temperature** and **humidity** sensor, if you have them. Your own beats the
   weather service, as long as it is not standing in the sun. (The integration notices if it is.)
3. Your **building type**. Only a starting value, measurements replace it after about a week.

Then add **rooms** and **windows** as subentries on the integration page:

- **Room**: name, temperature sensor, priority. A room with no sensor still works, it gets
  estimated, carries a confidence value, and is deliberately excluded from push notifications.
- **Window**: name, room, contact sensor, compass direction. The direction matters, the solar load
  is computed from it.

Everything else lives behind *Advanced* and has a sensible default.

> ⚙️ **Every setting explained**, including what stops working when you leave something out:
> **[docs/configuration.md](docs/configuration.md)**

### Removing it

Delete the config entry. Entities, devices, the sidebar panel and the stored calibration go with
it. Then uninstall through HACS, or delete the folder.

---

## Documentation

| | |
|---|---|
| 📖 **[How it works](docs/how-it-works.md)** | The physics, the thermal model, the rules, worked examples |
| ⚙️ **[Configuration](docs/configuration.md)** | Every option, what it does, what happens without it |
| 📊 **[Dashboard cards](docs/dashboard.md)** | Copy-and-paste Markdown and Mushroom cards, plus automation examples |
| 🖥️ **[ESPHome display](docs/esphome_example.yaml)** | A complete, working config for a small OLED or e-paper panel |
| 🔧 **[Troubleshooting](docs/troubleshooting.md)** | Symptoms, causes, known limitations |
| 🧭 **[Design decisions](docs/decisions.md)** | Why it is built this way, and where it deviates from the spec |
| 🛠️ **[Development](docs/development.md)** | Layout, tests, adding a rule, replaying your own history |

---

## Contributing

The decision logic lives in `custom_components/adaptive_ventilation/engine/`, a plain Python
package with **no Home Assistant import at all**. CI enforces that by running its tests in a job
where Home Assistant is not even installed. So you can change a rule and see its effect on fifteen
real scenarios in under a second:

```bash
pip install -r requirements-dev.txt
pytest tests
python scripts/replay.py --scenarios
```

Start with [docs/development.md](docs/development.md).

## Licence

[GPL-3.0](LICENSE)
