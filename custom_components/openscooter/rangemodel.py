"""Range model that lives in Home Assistant, so the range is known without the phone.

The phone only collects observations (one per 1 % of battery: km travelled with the conditions). This module learns
from every stored observation and answers "how far on N %" for any mode:

  km per 1 %  =  ridge regression on [1, eco, sport, v, v^2, soc, soc^2, temp, climb, v*eco, v*sport]
                 (newer observations weigh more, half-life 400) x a small per-mode correction
  range(N %, mode) = sum of the prediction over every remaining 1 % step (so the battery curve is learnt too)

Pure Python, no dependencies; refitted whenever new observations arrive.
"""
from __future__ import annotations

import math
from typing import Any

NB = 11
RIDGE = 0.05
HALF_LIFE = 400.0
MAX_ROWS = 3000
MIN_ROWS = 20
PRIOR_KM_PER_PCT = 0.4
MODES = ("sport", "drive", "eco")  # index used by the corrections


def _mode_idx(mode: str | None) -> int:
    return 0 if mode == "sport" else 2 if mode == "eco" else 1


def _features(eco: float, sport: float, speed: float, soc: float, temp: float, climb: float) -> list[float]:
    v = speed / 20.0
    t = (temp - 20.0) / 10.0
    c = climb / 10.0
    return [1.0, eco, sport, v, v * v, soc, soc * soc, t, c, v * eco, v * sport]


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


class RangeModel:
    def __init__(self) -> None:
        self.theta: list[float] | None = None
        self.bias = [1.0, 1.0, 1.0]
        self.samples = 0
        self.error_pct: float | None = None
        self.typ_speed = [18.0, 15.0, 12.0]
        self.typ_temp = 20.0
        self.typ_climb = 2.0

    @property
    def calibrated(self) -> bool:
        return self.theta is not None

    def _parse(self, line: str):
        c = line.split(",")
        if len(c) < 11:
            return None
        try:
            eco, sport, speed = float(c[5]), float(c[6]), float(c[4])
            temp = float(c[7]) if c[7] else None
            climb = float(c[8]) if c[8] else None
            return {"eco": eco, "sport": sport, "speed": speed, "temp": temp, "climb": climb,
                    "soc": float(c[9]), "y": float(c[10]), "w": 1.0 if c[2].strip() in ("1", "1.0") else 0.5}
        except ValueError:
            return None

    def fit(self, lines: list[str]) -> None:
        rows = [r for r in (self._parse(x) for x in lines[-MAX_ROWS:]) if r and r["y"] > 0]
        self.samples = len(rows)
        if not rows:
            return
        # typical conditions, per mode (used when the caller does not say how fast / how warm)
        for i, name in enumerate(MODES):
            sel = [r["speed"] for r in rows[-300:] if _mode_idx("sport" if r["sport"] > 0.5 else "eco" if r["eco"] > 0.5 else "drive") == i]
            if len(sel) >= 5:
                self.typ_speed[i] = sum(sel) / len(sel)
        temps = [r["temp"] for r in rows[-300:] if r["temp"] is not None]
        climbs = [r["climb"] for r in rows[-300:] if r["climb"] is not None]
        if temps:
            self.typ_temp = sum(temps) / len(temps)
        if climbs:
            self.typ_climb = sum(climbs) / len(climbs)
        if len(rows) < MIN_ROWS:
            return
        a = [[0.0] * NB for _ in range(NB)]
        bv = [0.0] * NB
        n = len(rows)
        feats = []
        for i, r in enumerate(rows):
            f = _features(r["eco"], r["sport"], r["speed"], r["soc"],
                          r["temp"] if r["temp"] is not None else self.typ_temp,
                          r["climb"] if r["climb"] is not None else self.typ_climb)
            feats.append(f)
            wt = r["w"] * math.pow(0.5, (n - 1 - i) / HALF_LIFE)
            for p in range(NB):
                bv[p] += wt * f[p] * r["y"]
                fp = wt * f[p]
                for q in range(NB):
                    a[p][q] += fp * f[q]
        for p in range(1, NB):
            a[p][p] += RIDGE
        sol = _solve(a, bv)
        if sol is None:
            return
        self.theta = sol
        # per-mode correction and honest error from the recent observations
        ratios: list[list[float]] = [[], [], []]
        apes: list[float] = []
        for f, r in list(zip(feats, rows))[-80:]:
            raw = sum(t * x for t, x in zip(sol, f))
            if raw > 0.05:
                m = _mode_idx("sport" if r["sport"] > 0.5 else "eco" if r["eco"] > 0.5 else "drive")
                ratios[m].append(r["y"] / raw)
                apes.append(abs(raw - r["y"]) / r["y"] * 100.0)
        self.bias = [min(1.3, max(0.75, sum(x) / len(x))) if len(x) >= 8 else 1.0 for x in ratios]
        self.error_pct = round(sum(apes) / len(apes), 1) if apes else None

    def per_pct(self, mode: str | None, soc: float, speed: float | None = None) -> float:
        m = _mode_idx(mode)
        if self.theta is None:
            return PRIOR_KM_PER_PCT
        f = _features(1.0 if mode == "eco" else 0.0, 1.0 if mode == "sport" else 0.0,
                      speed if speed is not None else self.typ_speed[m], soc, self.typ_temp, self.typ_climb)
        return min(1.5, max(0.08, sum(t * x for t, x in zip(self.theta, f)) * self.bias[m]))

    def range_km(self, battery: float | None, mode: str | None) -> float | None:
        if battery is None:
            return None
        b = max(0.0, min(100.0, float(battery)))
        whole = int(b)
        total = sum(self.per_pct(mode, (i - 0.5) / 100.0) for i in range(1, whole + 1))
        frac = b - whole
        if frac > 0:
            total += frac * self.per_pct(mode, (whole + 0.5 * frac) / 100.0)
        return round(total, 1)

    def summary(self) -> dict[str, Any]:
        return {"observations": self.samples, "calibrated": self.calibrated, "error_percent": self.error_pct,
                "bias": [round(x, 3) for x in self.bias]}
