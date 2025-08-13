"""Elite Colour Mixer — Flask API (Coloraide-powered, simple & fast)."""

from __future__ import annotations

import logging
import string
from typing import Any, TypeAlias, Callable

import numpy as np
from numpy.typing import NDArray
from flask import Flask, jsonify, render_template, request

# Subtractive mixer (faithful Kubelka–Munk)
from .kubelka import km_mix
from .hct_mixer import mix_hct

# Coloraide
from coloraide import Color as _Base
from coloraide.spaces.cam16_ucs import CAM16UCS, CAM16JMh
from coloraide.spaces.okhsv import Okhsv
from coloraide.spaces.okhsl import Okhsl

RGB01: TypeAlias = NDArray[np.float32]
Interpolator: TypeAlias = Callable[[float], RGB01]
Factory: TypeAlias = Callable[[RGB01, RGB01], Interpolator]
SpaceSpec: TypeAlias = tuple[str, dict[str, Any]]


class MixerEngine:
    """Encapsulate conversions + straightforward palette builders."""

    class Color(_Base):
        pass

    # Register only what we actually use.
    Color.register([CAM16JMh(), CAM16UCS(), Okhsv(), Okhsl()])

    # Declarative map for Coloraide-driven algos (no duplicate keys).
    _SPACE_MAP: dict[str, SpaceSpec] = {
        "srgb": ("srgb", {}),  # gamma-encoded
        "linear": ("srgb-linear", {}),  # linear-light
        "oklab": ("oklab", {}),
        "okhsv": ("okhsv", {"hue": "shorter"}),
        "okhsl": ("okhsl", {"hue": "shorter"}),
        "cam16ucs": ("cam16-ucs", {}),
        "cam16jmh": ("cam16-jmh", {"hue": "shorter"}),  # polar CAM16
    }

    def __init__(self) -> None:
        # Keep order stable for clients; include subtractive name.
        self._supported = tuple(list(self._SPACE_MAP.keys()) + ["km_sub", "mix_hct"])

    def supported(self) -> tuple[str, ...]:
        return self._supported

    # ----------------------- public API -----------------------
    def mix_palette(self, hex_a: str, hex_b: str, algo: str, n: int) -> list[str]:
        """Build a palette of n colours between A and B using `algo`."""
        algo = algo.lower()
        n = max(2, min(int(n), 512))

        if algo == "km_sub":
            return self._palette_km(hex_a, hex_b, n)

        if algo == "mix_hct":
            return mix_hct(hex_a, hex_b, n)

        if algo not in self._SPACE_MAP:
            raise ValueError(f"unknown algorithm '{algo}'")

        space, kw = self._SPACE_MAP[algo]
        # Fast-path for true linear-light sRGB: do LERP in NumPy, avoid Coloraide.
        if space == "srgb-linear" and not kw:
            a = self._hex_to_rgb01(hex_a)
            b = self._hex_to_rgb01(hex_b)
            ts = np.linspace(0.0, 1.0, n, dtype=np.float32)
            return [self._rgb01_to_hex((1.0 - t) * a + t * b) for t in ts]

        return self._palette_coloraide(hex_a, hex_b, n, space=space, **kw)

    # ----------------------- helpers --------------------------

    @staticmethod
    def _clamp01(x: RGB01) -> RGB01:
        return np.clip(x, 0.0, 1.0)

    @staticmethod
    def _canon_hex(s: str) -> str:
        """Normalise to '#rrggbb'; accept 3- or 6-digit hex only."""
        raw = s.strip().lstrip("#")
        if len(raw) == 3 and all(c in string.hexdigits for c in raw):
            raw = "".join(ch * 2 for ch in raw)
        if len(raw) != 6 or not all(c in string.hexdigits for c in raw):
            raise ValueError("hex must be 3 or 6 hex digits")
        return "#" + raw.lower()

    @staticmethod
    def _hex_to_rgb01(hex_str: str) -> RGB01:
        s = hex_str.lstrip("#")
        r, g, b = (int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
        return np.array([r, g, b], dtype=np.float32)

    @staticmethod
    def _rgb01_to_hex(rgb: RGB01) -> str:
        u8 = np.rint(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
        return f"#{u8[0]:02x}{u8[1]:02x}{u8[2]:02x}"

    # ------------------- palette builders ---------------------

    def _palette_coloraide(
        self, hex_a: str, hex_b: str, n: int, *, space: str, **kwargs
    ) -> list[str]:
        """Delegate to Coloraide's interpolator in `space` with explicit hue policy."""
        A = self.Color(hex_a)
        B = self.Color(hex_b)
        interp = self.Color.interpolate([A, B], space=space, method="linear", **kwargs)
        ts = np.linspace(0.0, 1.0, n, dtype=np.float32)
        out: list[str] = []
        for t in ts:
            col = interp(float(t)).convert("srgb")  # gamma-encoded 0..1
            rgb = np.array(col.coords(), dtype=np.float32)
            out.append(self._rgb01_to_hex(rgb))
        return out

    def _palette_km(self, hex_a: str, hex_b: str, n: int) -> list[str]:
        """Subtractive KM palette using our NumPy path."""
        a = self._hex_to_rgb01(hex_a)
        b = self._hex_to_rgb01(hex_b)
        ts = np.linspace(0.0, 1.0, n, dtype=np.float32)
        return [self._rgb01_to_hex(self._clamp01(km_mix(a, b, float(t)))) for t in ts]


# ---------------------------- Flask app factory -----------------------------


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    engine = MixerEngine()

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/mix")
    def mix():
        try:
            hex_a = engine._canon_hex(request.args.get("a", "ff0000"))
            hex_b = engine._canon_hex(request.args.get("b", "0000ff"))
        except Exception as e:
            return jsonify({"error": f"invalid color: {e}"}), 400

        algo = request.args.get("algo", "srgb")
        try:
            n = int(request.args.get("n", 21))
        except ValueError:
            return jsonify({"error": "n must be an integer"}), 400

        if algo.lower() not in engine.supported():
            return (
                jsonify(
                    {
                        "error": f"unknown algorithm '{algo}'",
                        "supported": engine.supported(),
                    }
                ),
                400,
            )

        try:
            palette = engine.mix_palette(hex_a, hex_b, algo, n)
        except Exception as exc:
            logging.exception("Interpolation failed")
            return jsonify({"error": str(exc)}), 500

        return jsonify(palette)

    return app


if __name__ == "__main__":
    # Do not enable debug for production; threaded=True is fine for this I/O profile.
    create_app().run(debug=False, threaded=True)
