# OpenScooter for Home Assistant

Scooter data and smart charging in Home Assistant, fed by the **OpenScooter** Android app.

## Install (HACS)
1. HACS → ⋮ → *Custom repositories* → add this repository, category **Integration**.
2. Install **OpenScooter**, restart Home Assistant.
3. *Settings → Devices & services → Add integration → OpenScooter*.

HACS needs `custom_components/` at the **root** of a repository, so publish the contents of this folder
(`custom_components/`, `hacs.json`, `README.md`) as its own repository (or copy `custom_components/openscooter`
into your Home Assistant `config/custom_components/` folder by hand).

## Pair the app
Open **OpenScooter** in the Home Assistant sidebar *using the address you want the app to use* (your public URL
to make it work from anywhere). Scan the QR code in the app (Home Assistant tab). No token is needed: the QR code
carries a private webhook address and a secret that are only known to your Home Assistant and your phone.

## Entities
Battery, speed, odometer, trip distance, drive mode, voltage, temperatures, last seen, connected, unlocked,
headlight, charging, position (device tracker), charge status / estimate / learned energy, and a **Smart charging** switch.

## Dashboard card
Added automatically. In the card picker choose **OpenScooter**, or in YAML: `type: custom:openscooter-card`.

## Smart charging
*Settings → Devices & services → OpenScooter → Configure*: pick the plug (switch), its power sensor (W) and, optionally,
its energy sensor (kWh). The plug is switched on when the scooter arrives home (inside the *Home* zone) below
the start level, and off when the charger power stays under the set threshold for the set time (charger finished).
Every finished charge teaches the integration how much energy 1 % of battery takes, after which charging also stops
at your chosen target %. While charging the battery % is estimated. Events: `openscooter_charge_started`,
`openscooter_charge_finished`. Services: `openscooter.start_charge`, `openscooter.stop_charge`.
