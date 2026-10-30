/* OpenScooter dashboard card. Add it from the card picker ("OpenScooter") or in YAML:
     type: custom:openscooter-card
   Entities are found automatically (sensor.openscooter_battery, ...). If you renamed the device,
   set  prefix: <your_device_name_slug>  */
class OpenScooterCard extends HTMLElement {
  static getStubConfig() { return {}; }
  setConfig(config) { this._config = config || {}; }
  getCardSize() { return 4; }

  set hass(hass) {
    this._hass = hass;
    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
      this._root.innerHTML = `
        <style>
          ha-card { padding: 16px; }
          .top { display:flex; align-items:center; gap:16px; }
          .ring { position:relative; width:96px; height:96px; flex:none; }
          .ring svg { width:96px; height:96px; transform: rotate(-90deg); }
          .ring .v { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; }
          .ring .v b { font-size:24px; line-height:1; }
          .ring .v span { font-size:11px; color: var(--secondary-text-color); }
          .name { font-size:18px; font-weight:500; }
          .state { color: var(--secondary-text-color); font-size:13px; margin-top:2px; }
          .chips { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
          .chip { display:flex; align-items:center; gap:6px; padding:6px 10px; border-radius:999px; background: var(--secondary-background-color); font-size:13px; }
          .chip.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
          .grid { display:grid; grid-template-columns: repeat(3, 1fr); gap:8px; margin-top:14px; }
          .cell { background: var(--secondary-background-color); border-radius:10px; padding:10px; text-align:center; }
          .cell b { display:block; font-size:16px; }
          .cell span { font-size:11px; color: var(--secondary-text-color); }
          .charge { margin-top:12px; font-size:13px; color: var(--secondary-text-color); }
        </style>
        <ha-card>
          <div class="top">
            <div class="ring">
              <svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="42" fill="none" stroke="var(--divider-color)" stroke-width="9"/>
                <circle id="arc" cx="50" cy="50" r="42" fill="none" stroke="var(--primary-color)" stroke-width="9" stroke-linecap="round" stroke-dasharray="263.9" stroke-dashoffset="263.9"/></svg>
              <div class="v"><b id="pct">???</b><span id="pctl">battery</span></div>
            </div>
            <div><div class="name">OpenScooter</div><div class="state" id="state">???</div>
              <div class="chips"><span class="chip" id="lock"></span><span class="chip" id="light"></span><span class="chip" id="mode"></span></div></div>
          </div>
          <div class="grid">
            <div class="cell"><b id="odo">???</b><span>Odometer</span></div>
            <div class="cell"><b id="trip">???</b><span>Last trip</span></div>
            <div class="cell"><b id="speed">???</b><span>Speed</span></div>
          </div>
          <div class="charge" id="charge"></div>
        </ha-card>`;
    }
    this._render();
  }

  _id(domain, key) { return `${domain}.${(this._config && this._config.prefix) || "openscooter"}_${key}`; }
  _s(domain, key) { const s = this._hass.states[this._id(domain, key)]; return s && s.state !== "unavailable" && s.state !== "unknown" ? s : null; }
  _q(id) { return this._root.getElementById(id); }

  _render() {
    const batt = this._s("sensor", "battery");
    const est = this._s("sensor", "battery_estimate");
    const charge = this._s("sensor", "charge_status");
    const charging = charge && charge.state === "charging";
    const value = charging && est ? Number(est.state) : batt ? Number(batt.state) : null;
    this._q("pct").textContent = value == null || isNaN(value) ? "???" : Math.round(value) + "%";
    this._q("pctl").textContent = charging ? "charging" : "battery";
    this._q("arc").setAttribute("stroke-dashoffset", String(263.9 * (1 - Math.max(0, Math.min(100, value || 0)) / 100)));

    const conn = this._s("binary_sensor", "connected");
    const seen = this._s("sensor", "last_seen");
    let state = conn && conn.state === "on" ? "Connected" : "Not connected";
    if (!(conn && conn.state === "on") && seen) state += " ?? last seen " + new Date(seen.state).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
    this._q("state").textContent = state;

    const unlocked = this._s("binary_sensor", "unlocked");
    const lock = this._q("lock");
    lock.textContent = unlocked ? (unlocked.state === "on" ? "Unlocked" : "Locked") : "Lock ?";
    lock.className = "chip" + (unlocked && unlocked.state === "on" ? " on" : "");
    const light = this._s("binary_sensor", "headlight");
    this._q("light").textContent = light && light.state === "on" ? "Light on" : "Light off";
    this._q("light").className = "chip" + (light && light.state === "on" ? " on" : "");
    const mode = this._s("sensor", "drive_mode");
    this._q("mode").textContent = mode ? mode.state : "Mode ?";

    const fmt = (s, unit, d = 1) => (s && !isNaN(Number(s.state)) ? Number(s.state).toFixed(d) + " " + unit : "???");
    this._q("odo").textContent = fmt(this._s("sensor", "odometer"), "km", 0);
    this._q("trip").textContent = fmt(this._s("sensor", "trip_distance"), "km");
    this._q("speed").textContent = fmt(this._s("sensor", "speed"), "km/h", 0);

    let text = "";
    if (charge) {
      const a = charge.attributes || {};
      if (charge.state === "charging") text = "Charging" + (est ? " ?? about " + Math.round(Number(est.state)) + "%" : "") + " ?? up to " + a.target_percent + "%";
      else if (charge.state === "waiting") text = "Plug on, waiting for the charger to draw power";
      else if (charge.state === "finished" && a.last_session) text = "Last charge: " + Number(a.last_session.energy_kwh).toFixed(2) + " kWh";
      else if (a.smart_charging) text = "Smart charging ready";
    }
    this._q("charge").textContent = text;
  }
}
customElements.define("openscooter-card", OpenScooterCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "openscooter-card", name: "OpenScooter", description: "Battery, lock, trip and charging status of your scooter", preview: false });
