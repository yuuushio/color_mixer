"""Elite Colour Mixer — Flask API (Coloraide-powered, simple & linear)."""

from __future__ import annotations

import logging
from typing import Any, TypeAlias, Callable

import numpy as np
from numpy.typing import NDArray
from flask import Flask, jsonify, render_template, request

# Subtractive mixer (faithful Kubelka–Munk)
from .kubelka import km_mix

# Coloraide
from coloraide import Color as _Base
from coloraide.spaces.cam16_ucs import CAM16UCS, CAM16JMh
from coloraide.spaces.okhsv import Okhsv
from coloraide.spaces.okhsl import Okhsl

RGB01: TypeAlias = NDArray[np.float32]
Interpolator: TypeAlias = Callable[[float], RGB01]
Factory: TypeAlias = Callable[[RGB01, RGB01], Interpolator]

# Per-algo space + kwargs passed to Coloraide
SpaceSpec: TypeAlias = tuple[str, dict[str, Any]]


class MixerEngine:
    """Encapsulate conversions + straightforward palette builders."""

    # Minimal project-local Color with the spaces we actually use.
    class Color(_Base):
        pass

    Color.register([CAM16JMh(), CAM16UCS(), Okhsv(), Okhsl()])

    # Declarative map for Coloraide-driven algos
    _SPACE_MAP: dict[str, SpaceSpec] = {
        "srgb": ("srgb", {}),
        "linear": ("srgb-linear", {}),
        "oklab": ("oklab", {}),
        "okhsv": ("okhsv", {"hue": "shorter"}),  # shortest-arc hue
        "cam16ucs": ("cam16-ucs", {}),
    }

    def __init__(self) -> None:
        self._supported = tuple(list(self._SPACE_MAP.keys()) + ["km_sub"])

    def supported(self) -> tuple[str, ...]:
        return self._supported

    # ----------------------- public API -----------------------
    def mix_palette(self, hex_a: str, hex_b: str, algo: str, n: int) -> list[str]:
        """Build a palette of n colours between A and B using `algo`."""
        algo = algo.lower()
        n = max(3, min(int(n), 256))

        if algo == "km_sub":
            return self._palette_km(hex_a, hex_b, n)

        if algo not in self._SPACE_MAP:
            raise ValueError(f"unknown algorithm '{algo}'")

        space, kw = self._SPACE_MAP[algo]
        return self._palette_coloraide(hex_a, hex_b, n, space=space, **kw)

    # ----------------------- helpers --------------------------

    @staticmethod
    def _clamp01(x: RGB01) -> RGB01:
        return np.clip(x, 0.0, 1.0)

    @staticmethod
    def _hex_to_rgb01(hex_str: str) -> RGB01:
        s = hex_str.lstrip("#")
        if len(s) != 6:
            raise ValueError("hex must be 6 digits RRGGBB")
        r, g, b = (int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
        return np.array([r, g, b], dtype=np.float32)

    @staticmethod
    def _rgb01_to_hex(rgb: RGB01) -> str:
        u8 = np.round(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
        return f"#{u8[0]:02x}{u8[1]:02x}{u8[2]:02x}"

    # ------------------- palette builders ---------------------

    def _palette_coloraide(
        self, hex_a: str, hex_b: str, n: int, *, space: str, **kwargs
    ) -> list[str]:
        """
        Build palette by delegating to Coloraide's interpolator in `space`.
        Reads linearly: build interpolator → sample t → convert → hex.
        """
        a = self.Color(hex_a)
        b = self.Color(hex_b)

        interp = self.Color.interpolate(
            [a.to_string(hex=True), b.to_string(hex=True)],
            space=space,
            method="linear",
            **kwargs,
        )

        ts = [i / (n - 1) for i in range(n)]
        out: list[str] = []
        for t in ts:
            col = interp(t).convert("srgb")
            # Convert through our rounding so server + UI stay consistent.
            rgb = np.array(col.coords(), dtype=np.float32)  # 0..1
            out.append(self._rgb01_to_hex(rgb))
        return out

    def _palette_km(self, hex_a: str, hex_b: str, n: int) -> list[str]:
        """Subtractive KM palette using our NumPy path."""
        a = self._hex_to_rgb01(hex_a)
        b = self._hex_to_rgb01(hex_b)
        ts = [i / (n - 1) for i in range(n)]
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
        hex_a = "#" + request.args.get("a", "ff0000").lstrip("#")[:6]
        hex_b = "#" + request.args.get("b", "0000ff").lstrip("#")[:6]
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
    create_app().run(debug=True, threaded=True)
