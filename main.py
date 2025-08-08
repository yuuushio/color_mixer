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
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>Elite Colour Mixer</title>
    <!-- <link rel="stylesheet" href="styles.css" /> -->
    <style>
      @import url("https://fonts.googleapis.com/css2?family=Oswald:wght@200..700&display=swap");

      :root {
        --bg: #eceff4;
        --fg: #2e3440;
        --primary: #5e81ac;
        --primary-light: #8fbcbb;
        --border: #d1d9e6;
        --input-bg: #ffffff;
      }
      *,
      *::before,
      *::after {
        box-sizing: border-box;
      }
      html,
      body {
        height: 100%;
      }
      body {
        font-family: system-ui, sans-serif;
        background: var(--bg);
        color: var(--fg);
        display: flex;
        justify-content: center;
        align-items: flex-start;
        padding:0;
        overflow:hidden;
      }
      .hard-bg {
        background: #eeaeca;
        /*
        background: radial-gradient(
          circle,
          rgba(238, 174, 202, 1) 0%,
          rgba(148, 187, 233, 1) 100%
        );*/
        /* padding: 2rem; */
      }
      .hard-bg,
      .filtered-bg {
        position: fixed;

        padding:0;
        display: flex;
        justify-content: center;

        align-items: flex-start;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        inset:0;
      }

      h1 {
        font-family: "Oswald";
        font-size: 1.2rem;
        padding: 0.5rem 1rem;
        letter-spacing: 0.1rem;
        margin-bottom: 1rem;
        margin-top: 0.5rem;
        font-weight: 400;
        /* box-shadow: rgba(0, 0, 0, 0.05) 0px 1px 2px 0px; */
        /* box-shadow: rgba(17, 17, 26, 0.1) 0px 1px 0px; */
      }
      .filtered-bg {

        padding: 4rem 2rem;
        background: rgba(236, 239, 244, 0.9);
        backdrop-filter: saturate(180%) blur(15px);
        overflow:auto;
      }
      .layout {
        opacity: 100%;
        display: flex;
        gap: 2rem;
        width: 100%;
        max-width: 1200px;
        align-items: flex-start;
      }

      .sidebar {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 1rem;
        flex: 0 0 235px; /* or whatever width you like */
      }

      .controls {
        background: rgba(216, 222, 233, 0.5);
        backdrop-filter: blur(6px);
        padding: 1rem;
        border: 1px solid rgba(76, 86, 106, 0.08);
        border-radius: 0.6rem;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
        display: flex;
        flex-direction: column;
      }
      .controls form {
        display: flex;
        flex-direction: column;
        gap: 1rem;
        width: 100%;
      }
      .controls label {
        display: flex;
        flex-direction: column;
        font-size: 0.7rem;
        width: 100%;
      }
      .controls input,
      .controls select,
      .mix-btn {
        margin-top: 0.25rem;
        padding: 0.5rem 0.75rem;
        font-size: 0.9rem;
        outline: none;
        border-radius: 0.4rem;
        background: rgba(216, 222, 233, 0.5);
        letter-spacing: 0.05rem;
        font-family: "Iosevka Nerd Font";
        border: 1px solid transparent;

        opacity: 80%;
        transition:
          border-color 0.2s,
          border 0.2s,
          opacity 0.1s,
          box-shadow 0.2s;
        width: 100%;
      }
      .controls input:focus {
        outline: none;
        opacity: 100%;

        border: 1px solid rgba(76, 86, 106, 0.1);
      }
      .mix-btn {
        opacity: 100%;
        background: #8fbcbb;
        color: #2e3440;
        border: none;
        cursor: pointer;
        opacity: 80%;
        border: 1px solid transparent;
        transition: opacity 0.2s;
      }
      .mix-btn:hover {
        background: #8fbcbb;
        opacity:100%;
      }

      .mix-btn button:active {

        background:#9dc3c3;
      }

      .swatches-panel {
        flex: 1;
      }
      #swatches {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(165px, 1fr));
        gap: 1rem;
        width: 100%;
        max-width: 960px;
      }
      .swatch {
        background: rgba(229, 233, 240, 0.1);
        border:none;
        border-radius: 10px;
        padding: 0.75rem;
        display: flex;
        flex-direction: column;
        align-items: center;
        box-shadow:
          rgba(0, 0, 0, 0.06) 0px 1px 3px 0px,
          rgba(0, 0, 0, 0.04) 0px 1px 2px 0px;
      }
      .color {
        width: 38px;
        height: 38px;
        border-radius: 0.6rem;
        border: 1px solid var(--border);
        margin-bottom: 0.75rem;
      }
      .value-wrapper {
        position: relative;
        display: inline-flex;
        justify-content: center;
        align-items: center;
        margin: 2px 0;
      }
      .hex,
      .rgb {
        font:
          0.82rem/1 "IBM Plex Mono",
          monospace;
        padding: 0.14rem 0.25rem;
        border-radius: 0.25rem;
        cursor: pointer;
        transition: 0.25s;
      }
      .hex:hover,
      .rgb:hover {
        background: rgba(0, 95, 204, 0.08);
        color: var(--primary);
      }
      .hex:active,
      .rgb:active {
        background: rgba(0, 95, 204, 0.18);
      }
      .tick {
        position: absolute;
        left: 100%;
        margin-left: 0.1rem;
        top: 50%;
        transform: translateY(-50%);
        font-size: 0.9rem;
        color: #a3be8c;
        pointer-events: none;
animation: tickFade var(--tick-duration, 1.5s) ease forwards;
opacity:1;
      }
@keyframes tickFade {
  50%, 90% { opacity: 1; }  /* visible most of the time */
  100%    { opacity: 0; }
}

      /* Algorithm dropdown */
      .algo-prefix-container {
        position: relative;
        margin-top: 0.25rem;
        display: flex;
        align-items: center;
        border: 1px solid transparent;
        outline: none;
        border-radius: 0.4rem;
        opacity: 80%;
        background: rgba(216, 222, 233, 0.5);
      }
      .algo-trigger {
        flex: 1;
        background: transparent;
        font-family: "Iosevka Nerd Font";
        padding: 0.5rem 0.75rem;
        font-size: 0.9rem;
        border: none;
        text-align: left;
        cursor: pointer;
      }

      .algo-outer-cont {
        display: flex;
        align-items: center;
        width: 100%;
        padding-right: 0.75rem;
      }
      .arrow {
        border: solid var(--fg);
        border-width: 0 2px 2px 0;
        display: inline-block;
        opacity: 50%;
        padding: 2px;
      }
      .down {
        transform: rotate(45deg);
        -webkit-transform: rotate(45deg);
      }
      .algo-menu {
        position: absolute;
        top: calc(100% + 4px);
        left: 0;
        width: 100%;
        background: rgba(229, 233, 240, 1);
        display: flex;
        flex-direction: column;
        transform-origin: top center;
        transform: scaleY(0);
        opacity: 0;
        transition:
          transform 0.1s ease-out,
          opacity 0.1s ease-out;
        z-index: 100;
        border: 1px solid rgba(76, 86, 106, 0.1);
        box-shadow:
          rgba(17, 17, 26, 0.05) 0px 1px 0px,
          rgba(17, 17, 26, 0.1) 0px 0px 8px;

        border-radius: 0.4rem;
        outline: none;
        padding: 0.2rem;
      }
      .algo-prefix-container.open .algo-menu {
        transform: scaleY(1);
        opacity: 1;
      }
      .algo-prefix-container.open {
        opacity: 100%;
        border: 1px solid rgba(76, 86, 106, 0.1);
      }
      .algo-item {
        padding: 0.5rem 0.75rem;
        margin-bottom: 0.2rem;
        background: transparent;
        border: none;
        border-radius: 0.4rem;
        text-align: left;
        cursor: pointer;
        transition: background 0.2s;

        font-size: 0.9rem;
        font-family: "Iosevka Nerd Font";
      }
      .algo-item:hover {
        background: rgba(216, 222, 233, 0.5);
        color: #5e81ac;
      }
      .algo-item.active {
        background: rgba(216, 222, 233, 0.5);
        color: #5e81ac;
      }
/* Hide native spinners */
input[type=number]::-webkit-outer-spin-button,
input[type=number]::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
input[type=number] { -moz-appearance: textfield; }

/* wrapper */
.number-field { position: relative; width: 100%; }
.number-field input[type=number] { padding-right: 2.25rem; }

/* buttons */
.number-field .step-increment-btn,
.number-field .step-decrement-btn {
font-family: "Iosevka Nerd Font", monospace;
  position: absolute;
  right: 4px;
  width: 1rem;
  height: 1rem;       /* square */
  display: grid;        /* dead-simple centering */
  place-items: center;
  padding: 0.05rem;
  font-size: .81rem;
  line-height: 1;       /* avoid weird vertical offset */
  border: none;
  border-radius: 0.2rem;
  background: none;
  color: var(--fg);
  cursor: pointer;
  opacity: .25;
  padding: 0;
  transition: background .12s ease, transform .06s ease, opacity .12s ease;
}

.number-field .step-increment-btn { top: 8px; }
.number-field .step-decrement-btn { bottom: 2px; }

.number-field .step-increment-btn:hover,
.number-field .step-decrement-btn:hover {
  opacity: .9;
}

.number-field .step-increment-btn:active,
.number-field .step-decrement-btn:active {
  transform: translateY(1px) scale(.98);
}

.number-field:focus-within .step-increment-btn,
.number-field:focus-within .step-decrement-btn { opacity: .85; }

.number-field .step-increment-btn[disabled],
.number-field .step-decrement-btn[disabled] {
  opacity: .35; cursor: default;
}
    </style>
  </head>
  <body>
    <div id="chaos" class="hard-bg">
      <div class="filtered-bg" >
        <div class="layout">
          <div class="sidebar">
            <div class="app-title">
              <h1>Colour Mixer</h1>
            </div>
            <div class="controls">
              <form id="mixform">
                <label>
                  Colour A
                  <input
                    type="text"
                    id="colA"
                    placeholder="#ff0000"
                    maxlength="7"
                    pattern="^#[0-9A-Fa-f]{6}$"
                    value="#ff0000"
                    required
                  />
                </label>
                <label>
                  Colour B
                  <input
                    type="text"
                    id="colB"
                    placeholder="#0000ff"
                    maxlength="7"
                    pattern="^#[0-9A-Fa-f]{6}$"
                    value="#0000ff"
                    required
                  />
                </label>
                <label class="algo-label">
                  Algorithm
                  <div class="algo-prefix-container" id="algo-dropdown">
                    <span class="algo-outer-cont">
                      <button
                        type="button"
                        class="algo-trigger"
                        id="algo-trigger"
                      >
                        sRGB γ-encoded
                      </button>
                      <span class="arrow down"></span>
                    </span>
                    <div class="algo-menu" id="algo-menu"></div>
                  </div>
                </label>
                <label>
                  Steps
  <div class="number-field">
    <input type="number" id="steps" value="21" min="3" max="64" />
    <button type="button" class="step-increment-btn"   aria-label="Increment">
    +
    </button>
    <button type="button" class="step-decrement-btn" aria-label="Decrement">
-
    </button>
  </div>
                </label>
                <button class="mix-btn" type="submit">Mix</button>
              </form>
            </div>
          </div>
          <div class="swatches-panel">
            <div id="swatches">
              <div class="swatch">
                <div class="color" style="background: rgb(255, 0, 0)"></div>
                <span class="value-wrapper"
                  ><span class="hex">#ff0000</span></span
                ><span class="value-wrapper"
                  ><span class="rgb">(255,0,0)</span></span
                >
              </div>
              <!-- Swatches will be injected here -->
            </div>
          </div>
        </div>
      </div>
    </div>

    <script>
document.querySelectorAll('input').forEach(el => {
  el.setAttribute('autocomplete', 'off');
  el.setAttribute('spellcheck', 'false');
  el.setAttribute('autocorrect', 'off');
  el.setAttribute('autocapitalize', 'off');
});
document.addEventListener('DOMContentLoaded', () => {
  const nf = document.querySelector('.number-field');
  if (!nf) return;
  const input = nf.querySelector('input[type=number]');
  const up    = nf.querySelector('.step-increment-btn');
  const down  = nf.querySelector('.step-decrement-btn');

  function clamp(val) {
    const min = input.min !== '' ? +input.min : -Infinity;
    const max = input.max !== '' ? +input.max :  Infinity;
    return Math.min(max, Math.max(min, val));
  }
  function updateDisabled() {
    const v = +input.value;
    const min = input.min !== '' ? +input.min : -Infinity;
    const max = input.max !== '' ? +input.max :  Infinity;
    up.disabled   = v >= max;
    down.disabled = v <= min;
  }
  function step(dir) {
    // Use native stepUp/stepDown when possible
    try { dir > 0 ? input.stepUp() : input.stepDown(); }
    catch { input.value = clamp((+input.value || 0) + dir * (+input.step || 1)); }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    updateDisabled();
  }

  up.addEventListener('click',   () => step(+1));
  down.addEventListener('click', () => step(-1));
  input.addEventListener('input', updateDisabled);
  updateDisabled();
});
      document.addEventListener("DOMContentLoaded", () => {
        // 1) Dropdown data & UI
        const ALGORITHMS = {
          srgb: "sRGB γ-encoded",
          linear: "Linear-light sRGB",
          oklab: "Oklab",
          okhsv: "OkHSV",
          cam16ucs: "CAM16-UCS",
          km_sub: "Kubelka–Munk",
        };
        let selected = "srgb";

        const dropdown = document.getElementById("algo-dropdown");
        const trigger = document.getElementById("algo-trigger");
        const menu = document.getElementById("algo-menu");

        function buildMenu() {
          menu.innerHTML = "";
          Object.entries(ALGORITHMS).forEach(([key, label]) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "algo-item";
            btn.textContent = label;
            btn.dataset.value = key;
            btn.addEventListener("click", () => selectAlgo(key));
            menu.appendChild(btn);
          });
        }

        function updateTrigger() {
          trigger.textContent = ALGORITHMS[selected];
          menu
            .querySelectorAll(".algo-item")
            .forEach((btn) =>
              btn.classList.toggle("active", btn.dataset.value === selected),
            );
        }

        function selectAlgo(key) {
          selected = key;
          dropdown.classList.remove("open");
          updateTrigger();
        }

function renderChaos(palette){
  const target = document.getElementById('chaos');
  if (!target || !Array.isArray(palette) || palette.length === 0) return;

  const first = palette[0];
  const last  = palette[palette.length - 1];
  const mid   = palette[Math.floor((palette.length - 1) / 2)]; // first of two middles

  // 45° linear gradient, explicit stops
    //`linear-gradient(135deg, ${first} 0%, ${mid} 50%, ${last} 100%)`;
  target.style.backgroundImage =
    `radial-gradient(circle, ${last} 0%, ${mid} 50%, ${first} 100%)`;
}

        trigger.addEventListener("click", (e) => {
          e.stopPropagation();
          dropdown.classList.toggle("open");
        });
        document.addEventListener("click", () =>
          dropdown.classList.remove("open"),
        );

        const form = document.getElementById("mixform");
        form.addEventListener("submit", async (e) => {
          e.preventDefault();

          const a = document.getElementById("colA").value.replace(/^#/, "");
          const b = document.getElementById("colB").value.replace(/^#/, "");
          const n = +document.getElementById("steps").value;

          // use our dropdown choice here
          const resp = await fetch(
            `/mix?${new URLSearchParams({
              algo: selected,
              a,
              b,
              n,
            })}`,
          );
          const data = await resp.json();

          const container = document.getElementById("swatches");
          container.innerHTML = "";

          data.forEach((hex) => {
            const rgbVals = hex
              .slice(1)
              .match(/../g)
              .map((h) => parseInt(h, 16));

            const sw = document.createElement("div");
            sw.className = "swatch";
            const chip = document.createElement("div");
            chip.className = "color";
            chip.style.background = hex;

            const wrapHex = document.createElement("span");
            wrapHex.className = "value-wrapper";
            const hexEl = document.createElement("span");
            hexEl.className = "hex";
            hexEl.textContent = hex;
            wrapHex.appendChild(hexEl);

            const wrapRgb = document.createElement("span");
            wrapRgb.className = "value-wrapper";
            const rgbEl = document.createElement("span");
            rgbEl.className = "rgb";
            rgbEl.textContent = `(${rgbVals.join(",")})`;
            wrapRgb.appendChild(rgbEl);

            // copy-on-click + tick
            [hexEl, rgbEl].forEach((el) => {
              el.addEventListener("click", async (ev) => {
                ev.stopPropagation();
                const txt = el === hexEl ? hex : `rgb${el.textContent}`;
                await navigator.clipboard.writeText(txt);

                const wrapper = el.parentNode;
const TICK_MS = 1000;
const tick = document.createElement('span');
tick.className = 'tick';
tick.textContent = '✓';
tick.style.setProperty('--tick-duration', `${TICK_MS}ms`);
wrapper.appendChild(tick);
tick.addEventListener('animationend', () => tick.remove());
              });
            });

            sw.append(chip, wrapHex, wrapRgb);
            container.append(sw);
          });

        renderChaos(data);
        });

        // Initialize everything
        buildMenu();
        updateTrigger();
        // auto-mix once on load
        setTimeout(() => form.dispatchEvent(new Event("submit")), 100);
      });
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
