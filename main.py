"""Minimal high‑performance colour‑mixer web app (Flask).

Implements six interpolation modes side‑by‑side so that one can evaluate
visual quality and performance.

Algorithms
----------
1. "srgb"        – naïve gamma‑encoded sRGB lerp (for reference).
2. "linear"      – linear‑light sRGB lerp (EOTF‑correct).
3. "oklab"       – Oklab Euclidean interpolation (≈ΔE‑uniform).
4. "okhsv"       – OkHSV polar interpolation (constant‑lightness hue wheel).
5. "cam16ucs"    – CAM16‑UCS Euclidean interpolation (requires *colour‑science*).
6. "km_sub"      – Kubelka–Munk subtractive mix on 36 sample spectrum (paint‑like).

Usage
-----
$ pip install flask numpy colour-science  # colour‑science optional
$ python color_mixer_app.py                # starts on http://127.0.0.1:5000

The root path serves a single‑file HTML demo.  All heavy maths stays in
NumPy for speed; small helper matrices are pre‑computed at import time
so every /mix call is O(steps) per algorithm.
"""

from __future__ import annotations

from kubelka import km_mix
import json
import math
from functools import lru_cache
from typing import Callable, Dict, List, Tuple
from colour.colorimetry import MSDS_CMFS

from coloraide import Color as _Base
from coloraide.spaces.cam16_ucs import CAM16UCS, CAM16JMh


class Color(_Base):
    """Project-local Color class with CAM16 support only."""


pass

# YOU MUST register *both* CAM16JMh (base model) *and* the UCS wrapper.
# Interpolation handlers come along automatically.
Color.register([CAM16JMh(), CAM16UCS()])

import numpy as np
from flask import Flask, jsonify, render_template_string, request
import logging

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------


def clamp01(x: np.ndarray) -> np.ndarray:
    return np.clip(x, 0.0, 1.0)


def hex_to_rgb01(hex_str: str) -> np.ndarray:
    hex_str = hex_str.lstrip("#")
    if len(hex_str) != 6:
        raise ValueError("hex must be 6 digits RRGGBB")
    r, g, b = (int(hex_str[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return np.array([r, g, b], dtype=np.float32)


# def rgb01_to_hex(rgb: np.ndarray) -> str:
#     rgb_u8 = (clamp01(rgb) * 255.0 + 0.5).astype(np.uint8)
#     return f"#{rgb_u8[0]:02x}{rgb_u8[1]:02x}{rgb_u8[2]:02x}"
def rgb01_to_hex(rgb: np.ndarray) -> str:
    """
    Convert an sRGB triplet in [0-1] to #RRGGBB using **true round-to-nearest**
    (exactly what `Math.round()` does for positive inputs).
    """
    rgb_u8 = np.round(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
    return f"#{rgb_u8[0]:02x}{rgb_u8[1]:02x}{rgb_u8[2]:02x}"


# -----------------------------------------------------------------------------
# Linear ↔︎ gamma‑encoded sRGB (IEC 61966‑2‑1)
# -----------------------------------------------------------------------------
SRGB_THRESHOLD = 0.04045
SRGB_EXPONENT = 2.4
SRGB_A = 0.055


@lru_cache(None)
def _srgb_forward_lut() -> np.ndarray:
    # 8‑bit → linear table for speed in demo UI (<1 µs lookup)
    x = np.arange(256, dtype=np.float32) / 255.0
    return np.where(
        x <= SRGB_THRESHOLD, x / 12.92, ((x + SRGB_A) / (1 + SRGB_A)) ** SRGB_EXPONENT
    )


@lru_cache(None)
def _srgb_inverse_lut() -> np.ndarray:
    # linear → 8‑bit
    x = np.linspace(0, 1, 4096, dtype=np.float32)  # more resolution back‑map
    g = np.where(
        x <= 0.0031308,
        x * 12.92,
        (1 + SRGB_A) * np.power(x, 1 / SRGB_EXPONENT) - SRGB_A,
    )
    return clamp01(g)


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    return _srgb_forward_lut()[(rgb * 255.0 + 0.5).astype(np.uint8)]


def linear_to_srgb(rgb_lin: np.ndarray) -> np.ndarray:
    # Index into inverse LUT (could use vectorised conditional; LUT is faster for bulk)
    idx = np.minimum((rgb_lin * 4095 + 0.5).astype(int), 4095)
    return _srgb_inverse_lut()[idx]


# -----------------------------------------------------------------------------
# Oklab + OkHSV conversion helpers (© Bjørn Ottosson, MIT licence)
# -----------------------------------------------------------------------------
_M1 = np.array(
    [
        [0.8189330101, 0.3618667424, -0.1288597137],
        [0.0329845436, 0.9293118715, 0.0361456387],
        [0.0482003018, 0.2643662691, 0.6338517070],
    ],
    dtype=np.float32,
)
_M2 = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ],
    dtype=np.float32,
)
_M1_INV = np.linalg.inv(_M1)


def srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    lrgb = srgb_to_linear(rgb)
    lms = _M1 @ lrgb
    lms_cbrt = np.cbrt(lms)
    return _M2 @ lms_cbrt


def oklab_to_srgb(oklab: np.ndarray) -> np.ndarray:
    l, a, b = oklab
    lms_cbrt = np.array(
        [
            l + 0.3963377774 * a + 0.2158037573 * b,
            l - 0.1055613458 * a - 0.0638541728 * b,
            l - 0.0894841775 * a - 1.2914855480 * b,
        ],
        dtype=np.float32,
    )
    lms = lms_cbrt**3
    lrgb = _M1_INV @ lms
    return linear_to_srgb(lrgb)


# -----------------------------------------------------------------------------
# OkHSV (see https://bottosson.github.io/posts/colorpicker/)
# -----------------------------------------------------------------------------


def okhsv_to_srgb(h: float, s: float, v: float) -> np.ndarray:
    # Highly simplified, adequate for demo – not full gamut mapping
    a_ = s * math.cos(2 * math.pi * h)
    b_ = s * math.sin(2 * math.pi * h)
    oklab = np.array([v, a_, b_], dtype=np.float32)
    return oklab_to_srgb(oklab)


def srgb_to_okhsv(rgb: np.ndarray) -> Tuple[float, float, float]:
    oklab = srgb_to_oklab(rgb)
    h = math.atan2(oklab[2], oklab[1]) / (2 * math.pi)
    s = math.hypot(oklab[1], oklab[2])
    v = oklab[0]
    return h % 1.0, s, v


# --------------------------------------------------------------------------
# CAM16-UCS via Coloraide  (tiny, fast, self-contained)
# --------------------------------------------------------------------------


def srgb_to_cam16ucs(rgb: np.ndarray) -> np.ndarray:  # → J′ a′ b′
    col = Color(rgb01_to_hex(rgb))  # sRGB → Coloraide
    j, a_, b_ = col.convert("cam16-ucs").coords()
    return np.array([j, a_, b_], dtype=np.float32)


def cam16ucs_to_srgb(ucs: np.ndarray) -> np.ndarray:  # J′ a′ b′ → sRGB
    j, a_, b_ = ucs
    col = Color(f"cam16-ucs {j} {a_} {b_}")
    return hex_to_rgb01(col.convert("srgb").to_string(hex=True))


_HAVE_CAM16 = True


# -----------------------------------------------------------------------------
# Interpolator registry
# -----------------------------------------------------------------------------
Interpolator = Callable[[np.ndarray, np.ndarray, float], np.ndarray]


def lerp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    return (1 - t) * a + t * b


def srgb_interp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    return lerp(a, b, t)


def linear_interp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    return clamp01(linear_to_srgb(lerp(srgb_to_linear(a), srgb_to_linear(b), t)))


def oklab_interp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    return clamp01(oklab_to_srgb(lerp(srgb_to_oklab(a), srgb_to_oklab(b), t)))


def okhsv_interp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    h1, s1, v1 = srgb_to_okhsv(a)
    h2, s2, v2 = srgb_to_okhsv(b)
    # shortest‑arc hue interpolation
    dh = ((h2 - h1 + 0.5) % 1) - 0.5
    h = (h1 + dh * t) % 1.0
    s = s1 + (s2 - s1) * t
    v = v1 + (v2 - v1) * t
    return clamp01(okhsv_to_srgb(h, s, v))


def cam16_interp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    try:
        hex_a = rgb01_to_hex(a)
        hex_b = rgb01_to_hex(b)
        logging.debug(f"[CAM16-UCS]  t={t:.3f}   A={hex_a}   B={hex_b}")

        # Build a fresh interpolator (cheap: two parses + small lambda)
        lerp = Color.interpolate([hex_a, hex_b], space="cam16-ucs", method="linear")

        col = lerp(t)  # Color object
        logging.debug(f"[CAM16-UCS]  → {col}")

        rgb = hex_to_rgb01(col.convert("srgb").to_string(hex=True))
        logging.debug(f"[CAM16-UCS]  sRGB {rgb}\n")
        return rgb

    except Exception:
        logging.exception("CAM16-UCS interpolation failed")
        raise  # let /mix return 500 → JS


INTERPOLATORS: Dict[str, Interpolator] = {
    "srgb": srgb_interp,
    "linear": linear_interp,
    "oklab": oklab_interp,
    "okhsv": okhsv_interp,
    "cam16ucs": cam16_interp,
    "km_sub": km_mix,
}

# -----------------------------------------------------------------------------
# Flask app
# -----------------------------------------------------------------------------
app = Flask(__name__)

INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Elite Colour Mixer</title>
<style>
:root {
  --bg: #ffffff;
  --fg: #333333;
  --primary: #1f8ef1;
  --primary-dark: #106cbf;
  --border: #dddddd;
  --input-bg: #f9f9f9;
  --card-bg: #ffffff;
  --shadow: rgba(0,0,0,0.05);
}
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--bg);
  color: var(--fg);
  display: flex; flex-direction: column; align-items: center;
}
h1 {
  font-size: 2rem; margin-bottom: 1rem; font-weight: 600;
}
form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  width: 100%; max-width: 600px;
  margin-bottom: 2rem;
}
label {
  display: flex; flex-direction: column;
  font-size: 0.9rem;
}
input[type="text"], select, input[type="number"] {
  margin-top: 0.25rem;
  padding: 0.5rem 0.75rem;
  font-size: 1rem;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--input-bg);
  transition: border-color 0.2s, box-shadow 0.2s;
}
input[type="text"]:focus, select:focus, input[type="number"]:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(31,142,241,0.2);
}
button {
  grid-column: span 2; justify-self: end;
  padding: 0.75rem 1.5rem;
  font-size: 1rem; font-weight: 500;
  color: #fff;
  background: var(--primary);
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s, transform 0.1s, box-shadow 0.2s;
}
button:hover {
  background: var(--primary-dark);
  box-shadow: 0 4px 12px var(--shadow);
}
button:active {
  transform: translateY(1px);
}
#swatches {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 1rem;
  width: 100%; max-width: 960px;
}
.swatch {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1rem;
  box-shadow: 0 2px 6px var(--shadow);
  display: flex; flex-direction: column; align-items: center;
  transition: box-shadow 0.2s, transform 0.2s;
}
.swatch:hover {
  box-shadow: 0 4px 14px var(--shadow);
  transform: translateY(-2px);
}
.swatch .color {
  width: 3rem; height: 3rem;
  border-radius: 4px;
  margin-bottom: 0.75rem;
  border: 1px solid var(--border);
}
.swatch .code {
  font-family: monospace;
  font-size: 0.85rem;
  text-align: center;
  word-break: break-all;
}
</style>
</head>
<body>
<h1>Elite Colour Mixer</h1>
<form id="mixform">
  <label>Colour A
    <input type="text" id="colA" placeholder="#ff0000" maxlength="7" pattern="^#[0-9A-Fa-f]{6}$" value="#ff0000" required>
  </label>
  <label>Colour B
    <input type="text" id="colB" placeholder="#0000ff" maxlength="7" pattern="^#[0-9A-Fa-f]{6}$" value="#0000ff" required>
  </label>
  <label>Algorithm
    <select id="algo">
      <option value="srgb">sRGB (γ-encoded)</option>
      <option value="linear">Linear-light sRGB</option>
      <option value="oklab">Oklab</option>
      <option value="okhsv">OkHSV</option>
      <option value="cam16ucs">CAM16-UCS</option>
      <option value="km_sub">Kubelka–Munk (subtractive)</option>
    </select>
  </label>
  <label>Steps
    <input type="number" id="steps" value="21" min="3" max="64">
  </label>
  <button type="submit">Mix Colours</button>
</form>
<div id="swatches"></div>
<script>
(async function() {
  const form = document.getElementById('mixform');
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const a = document.getElementById('colA').value.replace(/^#/, '');
    const b = document.getElementById('colB').value.replace(/^#/, '');
    const algo = document.getElementById('algo').value;
    const steps = +document.getElementById('steps').value;
    const qs = new URLSearchParams({ algo, a, b, n: steps });
    const resp = await fetch(`/mix?${qs}`);
    const data = await resp.json();
    const container = document.getElementById('swatches');
    container.innerHTML = '';
    data.forEach(hex => {
      const rgb = hex.slice(1).match(/../g).map(h => parseInt(h, 16));
      const sw = document.createElement('div'); sw.className = 'swatch';
      const col = document.createElement('div'); col.className = 'color'; col.style.background = hex;
      const txt = document.createElement('div'); txt.className = 'code'; txt.textContent = `${hex}  rgb(${rgb.join(',')})`;
      sw.append(col, txt);
      container.append(sw);
    });
  });
  // initial mix
  setTimeout(() => document.querySelector('button').click(), 200);
})();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/mix")
def mix():
    hex_a = "#" + request.args.get("a", "#ff0000").lstrip("#")
    hex_b = "#" + request.args.get("b", "#0000ff").lstrip("#")
    algo = request.args.get("algo", "srgb")
    n = int(request.args.get("n", 21))
    n = max(3, min(n, 256))

    if algo not in INTERPOLATORS:
        return jsonify({"error": f"unknown algorithm {algo}"}), 400

    try:
        rgb_a = hex_to_rgb01(hex_a)
        rgb_b = hex_to_rgb01(hex_b)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    interp = INTERPOLATORS[algo]

    palette: List[str] = []
    for i in range(n):
        t = i / (n - 1)
        try:
            rgb = interp(rgb_a, rgb_b, t)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        palette.append(rgb01_to_hex(rgb))
    return jsonify(palette)


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
