"""Minimal high-performance colour-mixer web app (Flask)."""

from __future__ import annotations
import math
import logging
from functools import lru_cache
from typing import Callable, Dict, List, Tuple

import numpy as np
from flask import Flask, jsonify, render_template, request

# your subtractive mixer
from .kubelka import km_mix

# Coloraide (CAM16-UCS)
from coloraide import Color as _Base
from coloraide.spaces.cam16_ucs import CAM16UCS, CAM16JMh


# -----------------------------------------------------------------------------
# App setup
# -----------------------------------------------------------------------------
# If app.py lives in src/color_mixer/, and you have:
#   src/color_mixer/templates/index.html
#   src/color_mixer/static/{styles.css,script.js}
# this works out of the box.
app = Flask(__name__, static_folder="static", template_folder="templates")

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")


# -----------------------------------------------------------------------------
# Coloraide wrapper (CAM16)
# -----------------------------------------------------------------------------
class Color(_Base):
    """Project-local Color class with CAM16 support only."""

    pass


# Register CAM16 models
Color.register([CAM16JMh(), CAM16UCS()])


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


def rgb01_to_hex(rgb: np.ndarray) -> str:
    """True round-to-nearest like Math.round for positive inputs."""
    rgb_u8 = np.round(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
    return f"#{rgb_u8[0]:02x}{rgb_u8[1]:02x}{rgb_u8[2]:02x}"


# -----------------------------------------------------------------------------
# Linear ↔ gamma-encoded sRGB
# -----------------------------------------------------------------------------
SRGB_THRESHOLD = 0.04045
SRGB_EXPONENT = 2.4
SRGB_A = 0.055


@lru_cache(None)
def _srgb_forward_lut() -> np.ndarray:
    x = np.arange(256, dtype=np.float32) / 255.0
    return np.where(
        x <= SRGB_THRESHOLD, x / 12.92, ((x + SRGB_A) / (1 + SRGB_A)) ** SRGB_EXPONENT
    )


@lru_cache(None)
def _srgb_inverse_lut() -> np.ndarray:
    x = np.linspace(0, 1, 4096, dtype=np.float32)
    g = np.where(
        x <= 0.0031308,
        x * 12.92,
        (1 + SRGB_A) * np.power(x, 1 / SRGB_EXPONENT) - SRGB_A,
    )
    return clamp01(g)


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    return _srgb_forward_lut()[(rgb * 255.0 + 0.5).astype(np.uint8)]


def linear_to_srgb(rgb_lin: np.ndarray) -> np.ndarray:
    idx = np.minimum((rgb_lin * 4095 + 0.5).astype(int), 4095)
    return _srgb_inverse_lut()[idx]


# -----------------------------------------------------------------------------
# Oklab + OkHSV helpers
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


def okhsv_to_srgb(h: float, s: float, v: float) -> np.ndarray:
    a_ = s * math.cos(2 * math.pi * h)
    b_ = s * math.sin(2 * math.pi * h)
    oklab = np.array([v, a_, b_], dtype=np.float32)
    return oklab_to_srgb(oklab)


def srgb_to_okhsv(rgb: np.ndarray) -> Tuple[float, float, float]:
    o = srgb_to_oklab(rgb)
    h = math.atan2(o[2], o[1]) / (2 * math.pi)
    s = math.hypot(o[1], o[2])
    v = o[0]
    return h % 1.0, s, v


# -----------------------------------------------------------------------------
# CAM16-UCS via Coloraide
# -----------------------------------------------------------------------------
def srgb_to_cam16ucs(rgb: np.ndarray) -> np.ndarray:
    col = Color(rgb01_to_hex(rgb))
    j, a_, b_ = col.convert("cam16-ucs").coords()
    return np.array([j, a_, b_], dtype=np.float32)


def cam16ucs_to_srgb(ucs: np.ndarray) -> np.ndarray:
    j, a_, b_ = ucs
    col = Color(f"cam16-ucs {j} {a_} {b_}")
    return hex_to_rgb01(col.convert("srgb").to_string(hex=True))


# -----------------------------------------------------------------------------
# Interpolators
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
    dh = ((h2 - h1 + 0.5) % 1) - 0.5  # shortest-arc hue
    h = (h1 + dh * t) % 1.0
    s = s1 + (s2 - s1) * t
    v = v1 + (v2 - v1) * t
    return clamp01(okhsv_to_srgb(h, s, v))


def cam16_interp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    try:
        hex_a = rgb01_to_hex(a)
        hex_b = rgb01_to_hex(b)
        logging.debug(f"[CAM16-UCS] t={t:.3f} A={hex_a} B={hex_b}")
        interp = Color.interpolate([hex_a, hex_b], space="cam16-ucs", method="linear")
        col = interp(t)
        rgb = hex_to_rgb01(col.convert("srgb").to_string(hex=True))
        return rgb
    except Exception:
        logging.exception("CAM16-UCS interpolation failed")
        raise


INTERPOLATORS: Dict[str, Interpolator] = {
    "srgb": srgb_interp,
    "linear": linear_interp,
    "oklab": oklab_interp,
    "okhsv": okhsv_interp,
    "cam16ucs": cam16_interp,
    "km_sub": km_mix,
}


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


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


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # threaded=True keeps it snappy for multiple /mix calls
    app.run(debug=True, threaded=True)
