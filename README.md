# ATI Straton — Home Assistant Integration

**Repository:** <https://github.com/benkrebs/ATIStraton>

[Deutsche Fassung → README.de.md](README.de.md)

Local Home Assistant integration for **ATI Straton** aquarium LED fixtures. No
cloud, no vendor app — it talks directly to the light on your own network.

Developed and verified against a **Straton Flex G2 153** running firmware
**3.0.4**.

> [!WARNING]
> **This is a private hobby project. It is not affiliated with, endorsed by, or
> supported by ATI Aquaristik in any way.** The device API is undocumented and
> was reconstructed by observing the fixture's own web interface. It can change
> without notice with any firmware update.
>
> **Using this integration can damage your light, disrupt your lighting
> schedule, or harm the livestock that depends on it. You use it entirely at
> your own risk.** See [Risks and warnings](#risks-and-warnings) before you
> install anything.
> The code is mostly AI generated. Help and improvements highly welcome.

---

## Contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Installation](#installation)
- [Setup](#setup)
- [Usage](#usage)
- [All entities](#all-entities)
- [Viewing colours](#viewing-colours)
- [How writes are kept safe](#how-writes-are-kept-safe)
- [Risks and warnings](#risks-and-warnings)
- [Device security findings](#device-security-findings)
- [Known limitations](#known-limitations)
- [Development](#development)
- [Disclaimer](#disclaimer)
- [License](#license)

---

## What it does

### Monitoring

- Temperature per LED module, pushed live (the device reports every ~2 s)
- Online status per LED module
- Power draw and load percentage, including the device's warning and danger
  thresholds
- Operating mode — see [Operating mode](#operating-mode)

### Turning the light on and off

A **Lighting** `switch` sets intensity to 0 and restores the last active value
when switched back on. The daily curve is preserved throughout. The device has
no real power switch.

### Intensity control

A slider (`number`) sets the **global intensity** from 0 to 100 %, exactly like
the intensity slider in the fixture's own web interface. Changes take effect
within a few seconds.

The **shape of your daily curve is preserved**: every control point is scaled
relative to its unchanged original value, so only the overall brightness moves.
Measured on the test device: intensity 60 → 490 ADC, 30 → 294 ADC, 15 → 166 ADC.

### Light programs

Pick one of the fixture's light programs, together with an **acclimatisation
level**. Both are exposed as `select` entities.

| Level | For | Intensity |
|---|---|---|
| Acclimatisation phase | light-sensitive corals | 30 % |
| Light-adapted | light-adapted corals | 60 % |
| High-light adapted | strongly light-adapted corals | 90 % |

The acclimatisation level takes effect the next time a program is loaded — the
same behaviour as the device's own interface, where it is part of the loading
dialog. Your existing lighting window (start and end time) is preserved rather
than being overwritten by the program's default.

### Colours

All colour presets are listed with their full spectral composition. The
composition can be edited through the `ati_straton.set_color` service.

### Temperature guard

Automatically reduces intensity when the fixture gets too warm, and releases it
again step by step once it cools down. Fully configurable from the UI:

| Control | Meaning |
|---|---|
| `switch` **Temperature guard** | turns the control loop on and off |
| `number` **Cut-off temperature** | reduction starts at or above this value |
| `number` **Release temperature** | reduction is unwound below this value |
| `number` **Reduction step** | percentage removed per control step |

Between the two thresholds the reduction is held. This hysteresis is what stops
the loop from chattering at the threshold. Control steps happen **at most every
5 minutes** — see [Risks and warnings](#risks-and-warnings) for why.

Two sensors make the loop observable: **Guard state**
(`idle` · `reducing` · `holding` · `recovering` · `disabled`) and **Guard
reduction** in percent.

### Operating mode

A single sensor tells you who is currently in charge of the daily curve:

| State | Meaning |
|---|---|
| `normal` | the device is running its schedule, untouched by this integration |
| `manual_intensity` | intensity was set through Home Assistant |
| `guard` | the temperature guard is holding the curve down |

The guard takes precedence: while it is engaged, both the intensity slider and
the program selector are locked.

---

## Requirements

- Home Assistant 2025.1 or newer
- An ATI Straton reachable on your local network
- The username and password of the fixture's web interface

There are **no external Python dependencies**. The device speaks Socket.IO 2.x
(Engine.IO 3), for which this integration ships its own small client built on
`aiohttp`, which Home Assistant already provides. Pinning an old
`python-socketio` release would have risked breaking other integrations sharing
the same Python environment.

## Installation

### Via HACS

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/benkrebs/ATIStraton` as URL, category **Integration**
3. Install "ATI Straton", then restart Home Assistant

### Manually

Copy the `custom_components/ati_straton/` folder into your own
`config/custom_components/` and restart Home Assistant.

## Setup

**Settings → Devices & Services → Add Integration → ATI Straton**

| Field | Example |
|---|---|
| Host or IP address | `192.168.1.42` |
| Username | user of the fixture's web interface |
| Password | matching password |

Credentials are stored in Home Assistant's encrypted credential store. They are
never written to logs or diagnostics.

Under **Configure** you can then adjust:

| Option | Default | Meaning |
|---|---|---|
| Polling interval | 30 s | how often non-pushed values are refreshed |
| **Maximum intensity** | 100 % | hard ceiling — clamps *every* change made through this integration |

If you are unsure, lower **Maximum intensity** to whatever your tank normally
runs at. It is the simplest protection against a mistaken automation.

## Usage

### Setting intensity

```yaml
service: number.set_value
target:
  entity_id: number.ati_straton_intensity
data:
  value: 45
```

### Loading a program with an acclimatisation level

Set the level first, then the program — the level is read when the program
loads.

```yaml
- service: select.select_option
  target:
    entity_id: select.ati_straton_acclimatisation
  data:
    option: Acclimatisation phase

- service: select.select_option
  target:
    entity_id: select.ati_straton_light_program
  data:
    option: Community presets · Programm B
```

### Editing a colour's composition

Channels you do not list stay unchanged. Values range from 0 to 255.

```yaml
service: ati_straton.set_color
data:
  device_id: <your Straton>
  color: Farbe D
  values:
    B: 255
    V: 180
    LC: 120
```

Available colours and their current composition are attributes of the
**Colours** sensor.

#### Colour channels

> [!IMPORTANT]
> The keys under `values` are the **channel codes**, not the labels shown in the
> device's web interface. The two differ in one place: channel **`LC`** is
> displayed as **`C`**. The service expects `LC`.

Which channels your fixture has depends on the model. Look them up in the
`composition` attribute of the **Colours** sensor — those are exactly the keys
the service accepts.

Firmware 3.0.4 knows these channel codes:

| Code | Displayed | Likely meaning |
|---|---|---|
| `W` | W | White |
| `WW` | WW | Warm white |
| `CW` | CW | Cool white |
| `HW` | HW | White, further variant |
| `B` | B | Blue |
| `RB` | RB | Royal blue |
| `RB-V` | RB-V | Royal blue / violet mix |
| `V` | V | Violet |
| `UV` | UV | Ultraviolet |
| `LC` | **C** | Cyan |
| `R` | R | Red |
| `T5` | T5 | T5 fluorescent channel of the hybrid models |

The **Displayed** column is read from the device's own language file and is
therefore certain. The **meaning** is nowhere stated in the firmware — it is
inferred from common aquarium lighting nomenclature and may differ on individual
models.

The test device (Straton Flex G2 153) has six of them. Top to bottom, in the
order the sliders appear in the device's web interface:

| Slider in the interface | Key for the service |
|---|---|
| V | `V` |
| RB | `RB` |
| B | `B` |
| **C** | **`LC`** |
| W | `W` |
| R | `R` |

The order comes from the device's `sort` field and is not alphabetical.

The firmware additionally knows the long forms `blue`, `green`, `royalblue`,
`violett`, `violett405`, `violett425` and `white`. They do not appear in the test
device's colour data and are probably not usable as keys.

### Enabling the temperature guard

```yaml
- service: number.set_value
  target: { entity_id: number.ati_straton_guard_cutoff_temperature }
  data: { value: 48 }

- service: number.set_value
  target: { entity_id: number.ati_straton_guard_release_temperature }
  data: { value: 44 }

- service: switch.turn_on
  target: { entity_id: switch.ati_straton_temperature_guard }
```

Set the cut-off **below** the device's own temperature limit (60 °C by default),
otherwise two control loops fight each other.

---

## All entities

The integration creates **one** device that everything hangs off. The exact
entity IDs follow the name you give that device.

### Controls

| Entity | Type | Meaning |
|---|---|---|
| **Lighting** | `switch` | Off sets intensity to 0, On restores the last active value. The daily curve is preserved throughout, it is merely scaled to zero. The device has no real power switch. |
| **Intensity** | `number` | Global brightness 0–100 %. Behaves like the slider in the device's interface and preserves the shape of the curve. |
| **Light program** | `select` | Loads a program. **Overwrites the daily curve.** Attributes carry the program list, the colours in use and the full schedule. |
| **Acclimatisation level** | `select` | Acclimatisation phase, light-adapted or high-light adapted. Takes effect on the next program change. |
| **Temperature guard** | `switch` | Turns automatic reduction on heat on and off. |
| **Guard cut-off temperature** | `number` | Reduction starts at or above this value. |
| **Guard release temperature** | `number` | Reduction is unwound below this value. |
| **Guard reduction step** | `number` | Percentage removed per control step. |

Lighting, Intensity and Light program are **locked while the guard is engaged** —
otherwise two writers would compete for the same schedule.

### Readings

| Entity | Type | Meaning |
|---|---|---|
| **Operating mode** | `sensor` | `normal`, `manual_intensity` or `guard` — who currently drives the daily curve. |
| **Spot_SiriusPro 1–3** | `sensor` | Temperature of that LED module in °C. |
| **Spot_SiriusPro 1–3 connection** | `binary_sensor` | Whether the module responds. |
| **Power draw (raw)** | `sensor` | The device's raw ADC value, unitless. |
| **Load** | `sensor` | The same value as a percentage of the ceiling. |
| **Current warning** | `binary_sensor` | Device warning or danger threshold exceeded. |
| **Guard state** | `sensor` | `disabled`, `idle`, `reducing`, `holding` or `recovering`. |
| **Guard reduction** | `sensor` | Current reduction in percent. |
| **Colours** | `sensor` | Number of colours; the compositions are in the attributes. |

### What a "spot" is

The Straton is **one** fixture containing several LED modules. The device calls
them *spots*; *SiriusPro* is the model name of those modules. They are not
separate lamps — the integration groups them under the same device.

On the test unit (153 cm) there are three modules, each using two DMX addresses:

| Module | Addresses | Example temperature |
|---|---|---|
| Spot_SiriusPro 1 | 1 + 2 | 40.8 °C |
| Spot_SiriusPro 2 | 3 + 4 | 39.7 °C |
| Spot_SiriusPro 3 | 5 + 6 | 38.9 °C |

Each module has its **own temperature sensor** and reports its connection
separately — hence three temperatures and three connection sensors. Shorter or
longer models have correspondingly fewer or more modules.

One module running consistently warmer than the others is normal and depends on
its position inside the fixture. The **temperature guard always evaluates the
hottest module**, not the average.

---

## Viewing colours

You do **not** need a custom Lovelace component for this. Everything is exposed
as attributes and can be rendered with a Markdown card.

### All colours with their composition

```yaml
type: markdown
content: |
  {% set colors = state_attr('sensor.ati_straton_colours', 'colors') %}
  | Colour | V | RB | B | C | W | R |
  |---|--:|--:|--:|--:|--:|--:|
  {% for c in colors -%}
  | {{ c.name }} | {{ c.composition.V }} | {{ c.composition.RB }} |
  {{- c.composition.B }} | {{ c.composition.LC }} | {{ c.composition.W }} |
  {{- c.composition.R }} |
  {% endfor %}
```

Column **C** carries the internal key `LC` — see
[Colour channels](#colour-channels).

### Colours used by the selected program

A program rarely uses all colours. On the test device it is three out of ten:

```yaml
type: markdown
content: |
  {% set p = 'select.ati_straton_light_program' %}
  ### {{ states(p) }}

  {% for c in state_attr(p, 'colors_in_use') -%}
  **{{ c.name }}** — {% for k, v in c.composition.items() %}{{ k }} {{ v }}{{ ", " if not loop.last }}{% endfor %}
  {% endfor %}
```

### Daily schedule with colour changes

```yaml
type: markdown
content: |
  | Time | Intensity | Colour |
  |---|--:|---|
  {% for e in state_attr('select.ati_straton_light_program', 'schedule') -%}
  | {{ e.time }} | {{ e.intensity }} | {{ e.color }} |
  {% endfor %}
```

On the test device this produces a table like the following — `Farbe E` in
the morning, `Farbe D` during the day, back again in the evening:

| Time | Intensity | Colour |
|---|--:|---|
| 09:00 | 0 | Farbe E |
| 10:00 | 52.71 | Farbe E |
| 12:00 | 70.0 | Farbe D |
| 19:08 | 49.41 | Farbe D |
| 20:11 | 49.41 | Farbe E |
| 22:30 | 0 | Farbe E |

---

## How writes are kept safe

Every change goes through the same guarded path:

| Layer | What it does |
|---|---|
| **Range validation** | Intensity must be `0…100`, colour channels `0…255`, integers only. Anything else is rejected before a request is built |
| **Maximum intensity** | A configurable ceiling clamps *every* change made through this integration |
| **Backup before each write** | The previous state is stored via Home Assistant's `Store` before anything is sent |
| **Crash recovery** | If Home Assistant dies while the guard is engaged, the original curve is restored on the next start |
| **Single writer** | Intensity and program selection are locked while the guard is active, so two writers never fight |
| **Exact restore** | The guard works on a verbatim snapshot and writes it back unchanged, rather than recomputing from a formula |

The last point matters: the device's own intensity formula normalises every
control point, and real curves can contain points that deviate from it.
Recomputing would silently alter them, so the guard never does.

---

## Risks and warnings

> [!CAUTION]
> **An aquarium light is life-support equipment.** Corals and other livestock
> depend on a stable light regime. A wrong value, a failed write or an
> unattended automation can cause real harm. Nothing in this project has been
> reviewed or approved by the manufacturer.

**Every write replaces the entire schedule.** The device API offers nothing more
granular than a full-document `PUT`, and it carries no version identifier. If
you save something in the ATI web interface at the same time, one side silently
overwrites the other. The integration writes a backup before every change, but
it cannot detect the conflict.

**The guard modifies your daily curve.** While it is engaged, the curve values
are genuinely lowered on the device — this is not a temporary overlay. On
release, the exact previous state is written back. If Home Assistant is killed
mid-intervention, the integration detects this on the next start and restores
the curve. If it never starts again, **the curve stays lowered.**

**Writes wear out the device's flash memory.** Every guard step is a flash
write. The manufacturer publishes no endurance figure, so the interval is
deliberately conservative at 5 minutes. Do not lower it without understanding
that trade-off.

**Firmware updates can break everything.** The API is undocumented. An update
may rename fields, change semantics, or remove endpoints. After any firmware
update, verify the integration still behaves as expected before relying on it.

**Test with the tank in view.** When trying anything new, watch the light and
keep the ATI web interface open as a fallback. Changes made by this integration
can always be undone there.

---

## Device security findings

These concern the ATI Straton itself, not this integration. They were found
while reverse-engineering the API and are documented so you can judge the risk.

| # | Finding |
|---|---|
| **S-01** | `GET /api/user` returns the account's **internal account data** (unprotected) to any authenticated session |
| **S-02** | **No TLS.** Credentials cross your network in the clear on every login |
| **S-03** | `reset-device`, `reboot` and `delete-spot` are reachable with an ordinary session, without extra confirmation |
| **S-04** | `GET /api/network` returns the **network settings of the fixture's own access point** in plain text |

This integration **never calls** `/api/user` or `/api/network`; both are hard
blocked in the HTTP client and excluded from diagnostics. The destructive
endpoints are deliberately not exposed as entities, so no automation can trigger
them by accident.

Recommendations: do not reuse a password you use anywhere else, and put the
fixture on a separate IoT VLAN or WLAN.

---

## Known limitations

- **No per-channel control.** The socket path the web frontend contains for this
  (`color-preview` / `color-change`) has **no effect** on the device — verified
  with a protocol-correct client. Only overall intensity and stored colour
  compositions can be controlled.
- **`/api/status.channels` is not live output.** It reports zeros while the
  fixture is running. Channel value sensors were removed for this reason.
- **Intensity and program are locked while the guard is engaged**, to prevent two
  writers from fighting over the schedule.
- **Custom programs carry no time range.** The integration derives the lighting
  window from your existing curve so a program change does not shift your
  familiar times.

---

## Development

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/python -m ruff check custom_components tests
```

86 tests run against recorded device data in `tests/fixtures/` and need **no
hardware**. Expected values for intensity scaling and program loading were taken
from a traffic capture of the original web interface, so the integration
reproduces the device's own behaviour byte for byte.

`requirements.md` documents the full reverse-engineering process: every endpoint,
the data model, the measurements, and the dead ends.

For scripts that talk to a real device, copy `.env.example` to `.env`. Never put
a password in it — `.env.example` explains how to use the system keychain
instead. Both `.env` and `backups/` are excluded from the repository.

---

## Disclaimer

This is a **private hobby project**, developed for a single fixture and shared in
case it is useful to someone else.

**It has no connection to ATI Aquaristik GmbH & Co. KG.** The manufacturer has
neither developed, reviewed, approved nor endorsed it, and provides no support
for it. "ATI" and "Straton" are the manufacturer's trademarks and are used here
only to describe what the software talks to.

The device API is not public. It was reconstructed by observing the fixture's own
web interface and may change or disappear with any firmware update.

**The software is provided "as is", without warranty of any kind. Use is entirely
at your own risk.** Neither the author nor any contributor is liable for damage
to hardware, loss of your lighting configuration, harm to livestock, or any other
loss arising from its use. Using it may void your warranty.

If you are not comfortable with those terms, please do not install it.

---

## License

MIT — see [LICENSE](LICENSE).

Issues and pull requests: <https://github.com/benkrebs/ATIStraton/issues>
