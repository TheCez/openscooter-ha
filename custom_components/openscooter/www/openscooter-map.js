/* Tiny route map: OpenStreetMap tiles + the route as an SVG line. No library, no API key.
   Tiles come from tile.openstreetmap.org (usage policy: light use, attribution shown). */
const mercX = (lon) => (lon + 180) / 360;
const mercY = (lat) => {
  const r = (Math.max(-85, Math.min(85, lat)) * Math.PI) / 180;
  return (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2;
};

/** points: [[lat, lon, timeMs, kmh], ...]. Redraws when the container is resized. */
export function drawRoute(container, points) {
  container.style.position = "relative";
  container.style.overflow = "hidden";
  container.style.background = "#e8e4dc";
  const render = () => {
    const w = container.clientWidth, h = container.clientHeight;
    if (!w || !h || !points || points.length < 2) return;
    const lons = points.map((p) => mercX(p[1])), lats = points.map((p) => mercY(p[0]));
    const x0 = Math.min(...lons), x1 = Math.max(...lons), y0 = Math.min(...lats), y1 = Math.max(...lats);
    const pad = 28, T = 256;
    const fx = Math.max(x1 - x0, 1e-7), fy = Math.max(y1 - y0, 1e-7);
    const z = Math.max(2, Math.min(18, Math.floor(Math.log2(Math.max(1, Math.min((w - 2 * pad) / (T * fx), (h - 2 * pad) / (T * fy)))))));
    const world = T * Math.pow(2, z), n = 1 << z;
    const cx = ((x0 + x1) / 2) * world, cy = ((y0 + y1) / 2) * world;
    const ox = w / 2 - cx, oy = h / 2 - cy;
    let html = "";
    for (let tx = Math.floor((cx - w / 2) / T); tx <= Math.floor((cx + w / 2) / T); tx++)
      for (let ty = Math.floor((cy - h / 2) / T); ty <= Math.floor((cy + h / 2) / T); ty++) {
        if (ty < 0 || ty >= n) continue;
        const wx = ((tx % n) + n) % n;
        html += `<img src="https://tile.openstreetmap.org/${z}/${wx}/${ty}.png" alt="" draggable="false" ` +
          `style="position:absolute;left:${Math.round(ox + tx * T)}px;top:${Math.round(oy + ty * T)}px;width:${T}px;height:${T}px" loading="lazy" referrerpolicy="strict-origin-when-cross-origin">`;
      }
    const xy = points.map((p) => [ox + mercX(p[1]) * world, oy + mercY(p[0]) * world]);
    const d = xy.map((q, i) => (i ? "L" : "M") + q[0].toFixed(1) + " " + q[1].toFixed(1)).join("");
    const a = xy[0], b = xy[xy.length - 1];
    html += `<svg width="${w}" height="${h}" style="position:absolute;left:0;top:0">` +
      `<path d="${d}" fill="none" stroke="#fff" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>` +
      `<path d="${d}" fill="none" stroke="#1a73e8" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>` +
      `<circle cx="${a[0]}" cy="${a[1]}" r="8" fill="#fff"/><circle cx="${a[0]}" cy="${a[1]}" r="5" fill="#30d158"/>` +
      `<circle cx="${b[0]}" cy="${b[1]}" r="8" fill="#fff"/><circle cx="${b[0]}" cy="${b[1]}" r="5" fill="#ff453a"/></svg>` +
      `<div style="position:absolute;right:6px;bottom:4px;font:10px sans-serif;color:#333;background:rgba(255,255,255,.7);padding:1px 4px;border-radius:3px">© OpenStreetMap contributors</div>`;
    container.innerHTML = html;
  };
  render();
  if (container._ro) container._ro.disconnect();
  if (typeof ResizeObserver !== "undefined") { container._ro = new ResizeObserver(render); container._ro.observe(container); }
}

/** GPX 1.1 text for a ride. */
export function toGpx(name, points) {
  const seg = points.map((p) => `<trkpt lat="${p[0]}" lon="${p[1]}">${p[2] ? `<time>${new Date(p[2]).toISOString()}</time>` : ""}</trkpt>`).join("");
  return `<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="OpenScooter" xmlns="http://www.topografix.com/GPX/1/1"><trk><name>${name}</name><trkseg>${seg}</trkseg></trk></gpx>`;
}
