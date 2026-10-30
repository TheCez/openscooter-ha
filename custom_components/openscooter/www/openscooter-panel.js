import qrcode from "./qrcode.mjs";

/* Sidebar panel: pair the OpenScooter app. The link uses the address this page was opened with,
   so opening Home Assistant through the public URL gives a public (works-anywhere) QR code. */
class OpenScooterPanel extends HTMLElement {
  set hass(h) {
    this._hass = h;
    if (!this._started) { this._started = true; this._build(); this._load(); }
  }
  set narrow(_) {}
  set panel(_) {}
  set route(_) {}

  _build() {
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; background: var(--primary-background-color); min-height:100%; color: var(--primary-text-color); font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
        .wrap { max-width: 560px; margin: 0 auto; padding: 24px 16px 48px; }
        h1 { font-size: 24px; font-weight: 500; margin: 0 0 4px; }
        .sub { color: var(--secondary-text-color); margin-bottom: 20px; }
        .card { background: var(--card-background-color); border-radius: 14px; padding: 20px; box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.2)); margin-bottom: 16px; }
        .qr { display:flex; justify-content:center; padding: 12px; background:#fff; border-radius: 12px; width: fit-content; margin: 0 auto 14px; }
        .qr svg { width: 240px; height: 240px; display:block; }
        label { display:block; font-size: 12px; color: var(--secondary-text-color); margin: 12px 0 4px; }
        input { width:100%; box-sizing:border-box; padding:10px; border-radius:8px; border:1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--primary-text-color); font-size:14px; }
        .row { display:flex; gap:8px; margin-top:10px; }
        button { flex:1; padding:10px 14px; border:0; border-radius:8px; background: var(--primary-color); color: var(--text-primary-color, #fff); font-size:14px; cursor:pointer; }
        button.sec { background: var(--secondary-background-color); color: var(--primary-text-color); }
        ol { padding-left: 20px; margin: 0; line-height: 1.7; }
        .status { display:flex; align-items:center; gap:8px; }
        .dot { width:10px; height:10px; border-radius:50%; background: var(--disabled-text-color); }
        .dot.on { background: var(--success-color, #4caf50); }
        .warn { color: var(--warning-color, #ff9800); font-size: 13px; margin-top: 8px; }
        code { word-break: break-all; font-size: 12px; }
      </style>
      <div class="wrap">
        <h1>OpenScooter</h1>
        <div class="sub">Pair the app with this Home Assistant</div>
        <div class="card">
          <div class="status"><span class="dot" id="dot"></span><span id="status">Waiting for the app…</span></div>
        </div>
        <div class="card">
          <ol>
            <li>Open the OpenScooter app → <b>Home Assistant</b>.</li>
            <li>Tap <b>Scan QR code</b> and scan this code.</li>
          </ol>
          <div style="height:14px"></div>
          <div class="qr" id="qr"></div>
          <label>Address the app will use (taken from the page you opened)</label>
          <input id="url" />
          <div class="warn" id="warn" hidden></div>
          <div class="row">
            <button id="copy" class="sec">Copy pairing link</button>
            <button id="open">Open in the app</button>
          </div>
          <label>Pairing link (same as the QR code)</label>
          <code id="link"></code>
        </div>
      </div>`;
    const $ = (i) => this.shadowRoot.getElementById(i);
    this._el = { qr: $("qr"), url: $("url"), link: $("link"), warn: $("warn"), dot: $("dot"), status: $("status"), copy: $("copy"), open: $("open") };
    this._el.url.value = location.origin;
    this._el.url.addEventListener("input", () => this._draw());
    this._el.copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(this._link()); this._el.copy.textContent = "Copied"; setTimeout(() => (this._el.copy.textContent = "Copy pairing link"), 1500); } catch (e) {}
    });
    this._el.open.addEventListener("click", () => { window.location.href = this._link(); });   // on the phone: opens the app and pairs
    this._timer = setInterval(() => this._load(true), 5000);
  }

  disconnectedCallback() { clearInterval(this._timer); }

  async _load(statusOnly) {
    try {
      const r = await this._hass.callWS({ type: "openscooter/pairing" });
      this._p = r;
      if (!statusOnly) this._draw();
      const seen = r.last_seen ? new Date(r.last_seen) : null;
      const recent = seen && Date.now() - seen.getTime() < 10 * 60 * 1000;
      this._el.dot.className = "dot" + (recent ? " on" : "");
      this._el.status.textContent = seen
        ? (recent ? "App connected" : "App last seen " + seen.toLocaleString()) + (r.battery != null ? " · battery " + r.battery + " %" : "")
        : "Waiting for the app…";
    } catch (e) {
      this._el.status.textContent = "Could not load pairing data: " + (e.message || e);
    }
  }

  _link() {
    const p = this._p || {};
    const u = this._el.url.value.trim().replace(/\/+$/, "");
    return `openscooter://pair?u=${encodeURIComponent(u)}&w=${encodeURIComponent(p.webhook_id || "")}&s=${encodeURIComponent(p.secret || "")}`;
  }

  _draw() {
    if (!this._p) return;
    const link = this._link();
    const qr = qrcode(0, "M");
    qr.addData(link);
    qr.make();
    this._el.qr.innerHTML = qr.createSvgTag({ scalable: true, margin: 0 });
    this._el.link.textContent = link;
    const u = this._el.url.value.trim();
    const local = /^(https?:\/\/)?(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|[^./]+\.local|homeassistant)/i.test(u.replace(/^https?:\/\//, "")) || /^http:\/\//i.test(u);
    this._el.warn.hidden = !local;
    this._el.warn.textContent = local ? "This looks like a local address: the app will only reach Home Assistant at home. Open this page through your public URL to get a link that works anywhere." : "";
  }
}
customElements.define("openscooter-panel", OpenScooterPanel);
