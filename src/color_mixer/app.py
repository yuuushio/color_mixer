"""Elite Colour Mixer — Flask API (ColorAide-native, stripped & fast)."""

from __future__ import annotations

import logging
import string
from typing import Any

import numpy as np
from flask import Flask, jsonify, render_template, request

# Project-local algorithms
from .kubelka import km_mix
from .hct_tone import tonal_ramp
from .hct_mixer import mix_hct  # keep if you still expose "mix_hct"

# ColorAide
from coloraide import Color
from coloraide.spaces.cam16_ucs import CAM16UCS
from coloraide.spaces.cam16_jmh import CAM16JMh
from coloraide.spaces.okhsv import Okhsv
from coloraide.spaces.okhsl import Okhsl
from coloraide.spaces.hct import HCT  # type: ignore[attr-defined]


class MixerEngine:
    """Thin façade over ColorAide. Everything routes through Color.steps()."""

    class Color(Color):
        pass

    # Register only what we actually use.
    Color.register([CAM16UCS(), CAM16JMh(), Okhsv(), Okhsl()])

    # Public algo keys → (ColorAide space id, default kwargs)
    _SPACE_MAP: dict[str, tuple[str, dict[str, Any]]] = {
        "srgb": ("srgb", {}),
        "linear": ("srgb-linear", {}),
        "oklab": ("oklab", {}),
        "okhsv": ("okhsv", {}),
        "okhsl": ("okhsl", {}),
        "hct": ("hct", {}),
        "cam16ucs": ("cam16-ucs", {}),
        "cam16jmh": ("cam16-jmh", {}),
    }

    # Interpolator methods ColorAide accepts
    _METHODS = {
        "linear",
        "css-linear",
        "continuous",
        "bspline",
        "natural",
        "monotone",
        "catrom",
    }

    # Hue arc policies
    _HUE = {"shorter", "longer", "increasing", "decreasing", "specified"}

    def supported(self) -> tuple[str, ...]:
        # Include specials up front
        return tuple(list(self._SPACE_MAP.keys()) + ["km_sub", "hct_tone"])

    # --------------------------- main dispatch ---------------------------

    def mix_palette(self, hex_a: str, hex_b: str, algo: str, n: int) -> list[str]:
        algo = (algo or "srgb").lower()
        n = max(2, min(int(n), 512))

        # Special cases first
        if algo == "km_sub":
            return self._palette_km(hex_a, hex_b, n)

        if algo == "hct_tone":
            sched = request.args.get("schedule", "linear").lower()
            if sched not in ("ease", "linear", "shadow", "highlight"):
                sched = "linear"
            try:
                gamma = float(request.args.get("gamma", 1.35))
            except (TypeError, ValueError):
                gamma = 1.35
            n = max(3, min(n, 512))
            return tonal_ramp(hex_a, n, schedule=sched, gamma=gamma)

        # Pure ColorAide interpolation for everything else
        if algo not in self._SPACE_MAP:
            raise ValueError(f"unknown algorithm '{algo}'")

        space, defaults = self._SPACE_MAP[algo]
        method = (request.args.get("method") or "linear").lower()
        if method not in self._METHODS:
            method = "linear"

        hue = (request.args.get("hue") or defaults.get("hue") or "shorter").lower()
        if hue not in self._HUE:
            hue = defaults.get("hue") or "shorter"

        colors = self.Color.steps(
            [hex_a, hex_b],
            steps=n,
            space=space,
            out_space="srgb",
            method=method,
            hue=hue,
        )
        return [c.to_string(hex=True, fit={"method": "raytrace"}) for c in colors]

    # --------------------------- subtractive KM ---------------------------

    def _palette_km(self, hex_a: str, hex_b: str, n: int) -> list[str]:
        """Faithful KM subtractive mix; parse/format via ColorAide, math via NumPy."""
        a = np.asarray(self.Color(hex_a).convert("srgb").coords(), dtype=np.float32)
        b = np.asarray(self.Color(hex_b).convert("srgb").coords(), dtype=np.float32)
        t = np.linspace(0.0, 1.0, n, dtype=np.float32)
        out = []
        for ti in t:
            rgb = np.clip(km_mix(a, b, float(ti)), 0.0, 1.0)
            out.append(self.Color("srgb", rgb.tolist()).to_string(hex=True))
        return out


def _canon_hex(s: str) -> str:
    """Normalize to '#rrggbb'; accept 3- or 6-digit hex only."""
    raw = (s or "").strip().lstrip("#")
    if len(raw) == 3 and all(c in string.hexdigits for c in raw):
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6 or not all(c in string.hexdigits for c in raw):
        raise ValueError("hex must be 3 or 6 hex digits")
    return "#" + raw.lower()


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
            hex_a = _canon_hex(request.args.get("a", "ff0000"))
            hex_b = _canon_hex(request.args.get("b", "0000ff"))
        except Exception as e:
            return jsonify({"error": f"invalid color: {e}"}), 400

        algo = (request.args.get("algo") or "srgb").lower()
        try:
            n = int(request.args.get("n", 21))
        except ValueError:
            return jsonify({"error": "n must be an integer"}), 400

        if algo not in engine.supported():
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


# ------------------------------ utilities -------------------------------


def _to_bool(v: str | None) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


if __name__ == "__main__":
    create_app().run(debug=False, threaded=True)
