import { drawRoute } from "./openscooter-map.js";

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
              <div class="v"><b id="pct">–</b><span id="pctl">battery</span></div>
            </div>
            <div><div class="name">OpenScooter</div><div class="state" id="state">–</div>
              <div class="chips"><span class="chip" id="lock"></span><span class="chip" id="light"></span><span class="chip" id="mode"></span></div></div>
          </div>
          <div class="grid">
            <div class="cell"><b id="odo">–</b><span>Odometer</span></div>
            <div class="cell"><b id="trip">–</b><span>Last trip</span></div>
            <div class="cell"><b id="speed">–</b><span>Speed</span></div>
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
    this._q("pct").textContent = value == null || isNaN(value) ? "–" : Math.round(value) + "%";
    this._q("pctl").textContent = charging ? "charging" : "battery";
    this._q("arc").setAttribute("stroke-dashoffset", String(263.9 * (1 - Math.max(0, Math.min(100, value || 0)) / 100)));

    const conn = this._s("binary_sensor", "connected");
    const seen = this._s("sensor", "last_seen");
    let state = conn && conn.state === "on" ? "Connected" : "Not connected";
    if (!(conn && conn.state === "on") && seen) state += " · last seen " + new Date(seen.state).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
    const rng = this._s("sensor", "estimated_range");
    if (rng && !isNaN(Number(rng.state))) state += " · range ~" + Number(rng.state).toFixed(0) + " km";
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

    const fmt = (s, unit, d = 1) => (s && !isNaN(Number(s.state)) ? Number(s.state).toFixed(d) + " " + unit : "–");
    this._q("odo").textContent = fmt(this._s("sensor", "odometer"), "km", 0);
    this._q("trip").textContent = fmt(this._s("sensor", "trip_distance"), "km");
    this._q("speed").textContent = fmt(this._s("sensor", "speed"), "km/h", 0);

    let text = "";
    if (charge) {
      const a = charge.attributes || {};
      const hm = (m) => (m >= 60 ? Math.floor(m / 60) + " h " + Math.round(m % 60) + " min" : Math.round(m) + " min");
      const at = (s) => new Date(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      if (charge.state === "charging") {
        const st = this._s("sensor", "charge_started");
        const sp = this._s("sensor", "charge_start_percent");
        const rem = this._s("sensor", "charge_time_remaining");
        const pw = this._s("sensor", "charge_power");
        text = "Charging" + (st ? " since " + at(st.state) : "") + (sp ? " from " + Math.round(Number(sp.state)) + "%" : "") +
          (est ? " · about " + Math.round(Number(est.state)) + "%" : "") +
          (pw ? " · " + Math.round(Number(pw.state)) + " W" : "") +
          (rem ? " · " + hm(Number(rem.state)) + " left" : "") + " · up to " + a.target_percent + "%";
      } else if (charge.state === "waiting") text = "Plug on, waiting for the charger to draw power";
      else if (charge.state === "finished" && a.last_session) {
        const l = a.last_session;
        text = "Charged" + (l.end_percent != null ? " to " + Math.round(l.end_percent) + "%" : "") + " · " +
          Number(l.energy_kwh).toFixed(2) + " kWh in " + hm(l.duration_min);
      } else if (a.smart_charging) text = "Smart charging ready";
    }
    this._q("charge").textContent = text;
  }
}
customElements.define("openscooter-card", OpenScooterCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "openscooter-card", name: "OpenScooter", description: "Battery, lock, trip and charging status of your scooter", preview: false });


/* Route of the last ride on a map. Add it as  type: custom:openscooter-route-card  (optional: height: 260) */
class OpenScooterRouteCard extends HTMLElement {
  static getStubConfig() { return {}; }
  setConfig(config) { this._config = config || {}; }
  getCardSize() { return 5; }
  set hass(hass) {
    this._hass = hass;
    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
      this._root.innerHTML = `<style>ha-card{padding:0;overflow:hidden}.map{height:${(this._config && this._config.height) || 260}px}
        .info{padding:12px 16px;display:flex;justify-content:space-between;gap:8px;font-size:14px}.sub{color:var(--secondary-text-color);font-size:12px}
        .empty{padding:16px;color:var(--secondary-text-color)}</style>
        <ha-card><div class="map" id="map"></div><div class="info" id="info"></div></ha-card>`;
      this._last = null;
    }
    const sensor = hass.states[`sensor.${(this._config && this._config.prefix) || "openscooter"}_last_ride`];
    const key = sensor ? sensor.state : "none";
    if (key !== this._last) { this._last = key; this._load(); }
  }
  async _load() {
    const info = this._root.getElementById("info"), map = this._root.getElementById("map");
    try {
      const r = await this._hass.callWS({ type: "openscooter/rides", limit: 20 });
      const ride = r.rides.find((x) => x.has_track);   // newest ride with a route
      if (!ride) { map.style.display = "none"; info.innerHTML = '<div class="empty">No ride with a GPS route has been synced yet.</div>'; return; }
      const t = await this._hass.callWS({ type: "openscooter/track", ride_id: ride.id });
      map.style.display = "";
      drawRoute(map, t.points);
      const km = (ride.dist / 1000).toFixed(1), mins = Math.round((ride.end - ride.start) / 60000);
      info.innerHTML = `<div><b>${km} km</b><div class="sub">${new Date(ride.start).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</div></div>` +
        `<div style="text-align:right">${mins} min<div class="sub">${ride.b0 >= 0 && ride.b1 >= 0 ? "battery " + ride.b0 + "% → " + ride.b1 + "%" : ""}${t.simplified ? " · simplified route" : ""}</div></div>`;
    } catch (e) { info.innerHTML = '<div class="empty">Could not load the route.</div>'; }
  }
}
customElements.define("openscooter-route-card", OpenScooterRouteCard);
window.customCards.push({ type: "openscooter-route-card", name: "OpenScooter route", description: "Route of the last ride on a map", preview: false });


/* Charging card. Add it as  type: custom:openscooter-charge-card  (optional: prefix, history: 5)
   Everything comes from Home Assistant itself (the smart plug), so it works with or without the phone app. */
class OpenScooterChargeCard extends HTMLElement {
  static getStubConfig() { return {}; }
  setConfig(config) { this._config = config || {}; }
  getCardSize() { return 6; }

  set hass(hass) {
    this._hass = hass;
    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
      this._root.innerHTML = `
        <style>
          ha-card { padding: 18px; overflow: hidden; position: relative; }
          .head { display:flex; align-items:center; gap:18px; }
          .ring { position:relative; width:118px; height:118px; flex:none; }
          .ring svg { width:118px; height:118px; transform: rotate(-90deg); }
          .ring .c { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; }
          .ring .c b { font-size:30px; line-height:1; }
          .ring .c span { font-size:11px; color: var(--secondary-text-color); margin-top:2px; }
          .arc { transition: stroke-dashoffset .8s ease; }
          .charging .arc { filter: drop-shadow(0 0 6px var(--charge-color)); }
          .pulse { animation: pulse 2s ease-in-out infinite; }
          @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:.45 } }
          .title { font-size:18px; font-weight:500; }
          .status { margin-top:4px; font-size:13px; color: var(--secondary-text-color); }
          .pill { display:inline-block; padding:3px 10px; border-radius:999px; font-size:12px; margin-top:8px; background: var(--secondary-background-color); }
          .pill.on { background: var(--charge-color); color:#fff; }
          .grid { display:grid; grid-template-columns: repeat(3, 1fr); gap:8px; margin-top:16px; }
          .cell { background: var(--secondary-background-color); border-radius:12px; padding:10px 6px; text-align:center; }
          .cell b { display:block; font-size:16px; }
          .cell span { font-size:11px; color: var(--secondary-text-color); }
          .sec { margin-top:16px; font-size:11px; letter-spacing:.8px; text-transform:uppercase; color: var(--secondary-text-color); }
          .curve { margin-top:6px; height:84px; }
          .curve svg { width:100%; height:100%; display:block; }
          .hist .r { display:flex; justify-content:space-between; gap:8px; padding:8px 0; border-top:1px solid var(--divider-color); font-size:13px; }
          .hist .r:first-child { border-top:0; }
          .hist .d { color: var(--secondary-text-color); font-size:12px; }
          .learn { margin-top:14px; font-size:12px; color: var(--secondary-text-color); line-height:1.5; }
        </style>
        <ha-card id="card" style="--charge-color:#ff9f0a">
          <div class="head">
            <div class="ring"><svg viewBox="0 0 100 100">
                <circle cx="50" cy="50" r="43" fill="none" stroke="var(--divider-color)" stroke-width="8"/>
                <circle class="arc" id="arc" cx="50" cy="50" r="43" fill="none" stroke="var(--charge-color)" stroke-width="8" stroke-linecap="round" stroke-dasharray="270.2" stroke-dashoffset="270.2"/>
                <line id="tgt" x1="50" y1="2" x2="50" y2="12" stroke="var(--primary-text-color)" stroke-width="2" opacity=".6"/></svg>
              <div class="c"><b id="pct">–</b><span id="pctl">battery</span></div></div>
            <div><div class="title">Scooter charging</div><div class="status" id="status">–</div><span class="pill" id="pill">–</span></div>
          </div>
          <div class="grid">
            <div class="cell"><b id="started">–</b><span>Started</span></div>
            <div class="cell"><b id="from">–</b><span>From</span></div>
            <div class="cell"><b id="left">–</b><span>Time left</span></div>
            <div class="cell"><b id="power">–</b><span>Power</span></div>
            <div class="cell"><b id="energy">–</b><span>Energy</span></div>
            <div class="cell"><b id="target">–</b><span>Target</span></div>
          </div>
          <div id="curvebox"><div class="sec" id="curvelbl">Power</div><div class="curve" id="curve"></div></div>
          <div class="sec">Recent charges</div><div class="hist" id="hist"></div>
          <div class="learn" id="learn"></div>
        </ha-card>`;
      this._last = "";
    }
    const prefix = (this._config && this._config.prefix) || "openscooter";
    const st = hass.states[`sensor.${prefix}_charge_status`];
    const est = hass.states[`sensor.${prefix}_battery_estimate`];
    const key = (st ? st.state : "") + "|" + (est ? est.state : "") + "|" + Math.floor(Date.now() / 30000);
    if (key !== this._last) { this._last = key; this._load(); }
  }

  async _load() {
    if (this._busy) return;
    this._busy = true;
    try { this._d = await this._hass.callWS({ type: "openscooter/charge" }); this._render(); }
    catch (e) { this._root.getElementById("status").textContent = "Could not load charging data"; }
    this._busy = false;
  }

  _spark(el, curve, color) {
    if (!curve || curve.length < 3) { el.innerHTML = '<div style="color:var(--secondary-text-color);font-size:12px;padding-top:24px;text-align:center">The power curve appears as the charge goes on</div>'; return; }
    const W = 300, H = 84, maxT = Math.max(1, ...curve.map((c) => c[0])), maxW = Math.max(10, ...curve.map((c) => c[2]));
    const xy = curve.map((c) => [(c[0] / maxT) * W, H - 6 - (c[2] / maxW) * (H - 14)]);
    const d = xy.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join("");
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>` +
      `<path d="${d}L${W} ${H - 6}L0 ${H - 6}Z" fill="url(#g)"/><path d="${d}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
  }

  _render() {
    const $ = (i) => this._root.getElementById(i), d = this._d, c = d.current || {};
    const hm = (m) => (m == null ? "–" : m >= 60 ? Math.floor(m / 60) + " h " + Math.round(m % 60) + " min" : Math.round(m) + " min");
    const at = (s) => new Date(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const charging = c.status === "charging", waiting = c.status === "waiting", done = c.status === "finished";
    const last = (d.sessions || [])[d.sessions.length - 1];
    $("card").classList.toggle("charging", charging);
    const color = done ? "#30d158" : "#ff9f0a";
    $("card").style.setProperty("--charge-color", color);

    let value = null, label = "battery";
    if (charging && c.estimate != null) { value = c.estimate; label = "estimated"; }
    else if (done && last && last.end_percent != null) { value = last.end_percent; label = "charged"; }
    else { const b = this._hass.states[`sensor.${(this._config && this._config.prefix) || "openscooter"}_battery`]; if (b && !isNaN(Number(b.state))) { value = Number(b.state); label = "last seen"; } }
    $("pct").textContent = value == null ? "–" : (charging ? "~" : "") + Math.round(value) + "%";
    $("pctl").textContent = label;
    $("pctl").className = charging ? "pulse" : "";
    $("arc").setAttribute("stroke-dashoffset", String(270.2 * (1 - Math.max(0, Math.min(100, value || 0)) / 100)));
    const tgt = c.target || 100;
    $("tgt").setAttribute("transform", `rotate(${(tgt / 100) * 360} 50 50)`);
    $("tgt").style.display = c.enabled && tgt < 100 ? "" : "none";

    $("status").textContent = charging ? "Charging since " + (c.started_at ? at(c.started_at) : "–")
      : waiting ? "Plug on, waiting for the charger to draw power"
      : done && last ? "Charged to " + Math.round(last.end_percent ?? 100) + "% · " + Number(last.energy_kwh).toFixed(2) + " kWh"
      : c.enabled ? "Smart charging ready" : "Smart charging is off";
    const pill = $("pill");
    pill.textContent = charging ? "Charging" : waiting ? "Plug on" : done ? "Charged" : "Idle";
    pill.className = "pill" + (charging ? " on pulse" : done ? " on" : "");

    $("started").textContent = charging && c.started_at ? at(c.started_at) : "–";
    $("from").textContent = charging && c.start_percent != null ? Math.round(c.start_percent) + "%" : "–";
    $("left").textContent = charging ? hm(c.remaining_min) : "–";
    $("power").textContent = charging && c.power_w != null ? Math.round(c.power_w) + " W" : "–";
    $("energy").textContent = charging && c.energy_kwh != null ? Number(c.energy_kwh).toFixed(2) + " kWh" : "–";
    $("target").textContent = c.enabled ? tgt + "%" : "–";

    const live = charging ? d.live_curve : d.last_curve;
    $("curvelbl").textContent = charging ? "Power of this charge" : "Power of the last charge";
    this._spark($("curve"), live, color);

    const n = (this._config && this._config.history) || 5;
    const rows = (d.sessions || []).slice(-n).reverse();
    $("hist").innerHTML = rows.length ? rows.map((r) =>
      `<div class="r"><div>${new Date(r.start).toLocaleDateString([], { day: "numeric", month: "short" })} · ${at(r.start)}<div class="d">${r.start_percent != null ? Math.round(r.start_percent) + "%" : "?"} → ${r.end_percent != null ? Math.round(r.end_percent) + "%" : "?"}</div></div>` +
      `<div style="text-align:right">${Number(r.energy_kwh).toFixed(2)} kWh<div class="d">${hm(r.duration_min)}</div></div></div>`).join("")
      : '<div class="d" style="color:var(--secondary-text-color);font-size:13px">No finished charge yet.</div>';

    const m = d.model || {};
    $("learn").textContent = m.kwh_per_percent
      ? `Learned from ${m.sessions} charge${m.sessions === 1 ? "" : "s"}: 1% ≈ ${(m.kwh_per_percent * 1000).toFixed(0)} Wh` + (m.full_kwh ? ` · full battery ≈ ${m.full_kwh.toFixed(2)} kWh` : "") + ` · ${m.total_sessions} charges, ${m.total_kwh} kWh in total`
      : "The estimate appears after the first complete charge.";
  }
}
customElements.define("openscooter-charge-card", OpenScooterChargeCard);
window.customCards.push({ type: "openscooter-charge-card", name: "OpenScooter charging", description: "Live charge, power curve and charge history", preview: false });
