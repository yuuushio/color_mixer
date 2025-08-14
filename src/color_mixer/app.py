from __future__ import annotations

import logging
import string
from typing import Any, Mapping, cast

import numpy as np
from flask import Flask, jsonify, render_template, request

# Project-local algorithms
from .kubelka import km_mix
from .hct_tone import tonal_ramp, Schedule as ToneSchedule

# ColorAide
from coloraide import Color as CAColor
from coloraide.spaces.cam16_ucs import CAM16UCS
from coloraide.spaces.cam16_jmh import CAM16JMh
from coloraide.spaces.okhsv import Okhsv
from coloraide.spaces.okhsl import Okhsl
from coloraide.spaces.hct import HCT

log = logging.getLogger(__name__)


class C(Color := CAColor):
    pass


C.register([CAM16UCS(), CAM16JMh(), Okhsv(), Okhsl()])

SPACE_MAP: Mapping[str, tuple[str, dict[str, Any]]] = {
    "srgb": ("srgb", {}),
    "linear": ("srgb-linear", {}),  # linear-light sRGB
    "oklab": ("oklab", {}),
    "okhsv": ("okhsv", {"hue": "shorter"}),  # polar — specify hue policy
    "okhsl": ("okhsl", {"hue": "shorter"}),  # polar — specify hue policy
    "hct": ("hct", {}),  # Google HCT (ColorAide space)
    "cam16ucs": ("cam16-ucs", {}),
    "cam16jmh": ("cam16-jmh", {"hue": "shorter"}),  # polar CAM16
}

METHODS = {
    "linear",
    "css-linear",
    "continuous",
    "bspline",
    "natural",
    "monotone",
    # expose 'discrete' for visibly different behavior with two stops
    "discrete",
    # 'catrom' requires plugin registration in some builds; include only if you’ve enabled it
    # "catrom",
}

HUE_POLICIES = {"shorter", "longer", "increasing", "decreasing", "specified"}

FIT_HEX = {"method": "raytrace"}  # consistent gamut-fit for hex output


def canon_hex(s: str) -> str:
    """Normalize to '#rrggbb'; accept 3- or 6-digit hex only."""
    raw = (s or "").strip().lstrip("#")
    if len(raw) == 3 and all(c in string.hexdigits for c in raw):
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6 or not all(c in string.hexdigits for c in raw):
        raise ValueError("hex must be 3 or 6 hex digits")
    return "#" + raw.lower()


def parse_method(val: str | None) -> str:
    m = (val or "linear").strip().lower()
    return m if m in METHODS else "linear"


def parse_hue(val: str | None, default: str | None) -> str | None:
    if val:
        v = val.strip().lower()
        if v in HUE_POLICIES:
            return v
    return default if (default in HUE_POLICIES) else None


def supported_algorithms() -> tuple[str, ...]:
    return tuple(list(SPACE_MAP.keys()) + ["km_sub", "hct_tone"])


def mix_steps(
    hex_a: str, hex_b: str, *, space: str, method: str, hue: str | None, n: int
) -> list[str]:
    """Generic A→B interpolation via ColorAide in `space`."""
    steps = C.steps(
        [hex_a, hex_b],
        steps=max(2, min(int(n), 512)),
        space=space,
        out_space="srgb",
        method=method,
    )
    return [c.to_string(hex=True, fit=FIT_HEX) for c in steps]


def mix_km(hex_a: str, hex_b: str, n: int) -> list[str]:
    """Kubelka–Munk subtractive mix (faithful), using our NumPy path."""
    a = np.asarray(C(hex_a).convert("srgb").coords(), dtype=np.float32)
    b = np.asarray(C(hex_b).convert("srgb").coords(), dtype=np.float32)
    ts = np.linspace(0.0, 1.0, max(2, min(int(n), 512)), dtype=np.float32)
    out: list[str] = []
    for t in ts:
        rgb = np.clip(km_mix(a, b, float(t)), 0.0, 1.0)
        out.append(C("srgb", rgb.tolist()).to_string(hex=True))
    return out


def mix_hct_tone(seed: str, n: int, *, schedule: str, gamma: float) -> list[str]:
    """Google-style single-seed tonal ramp in HCT."""
    n = max(3, min(int(n), 512))
    _s = schedule if schedule in {"ease", "linear", "shadow", "highlight"} else "linear"
    sched: ToneSchedule = cast(ToneSchedule, _s)
    try:
        g = float(gamma)
    except Exception:
        g = 1.35
    return tonal_ramp(seed, n, schedule=sched, gamma=g)


# ----------------------------- Flask app ----------------------------------


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/mix")
    def mix():
        # Inputs
        try:
            a = canon_hex(request.args.get("a", "ff0000"))
            b = canon_hex(request.args.get("b", "0000ff"))
        except Exception as e:
            return jsonify({"error": f"invalid color: {e}"}), 400

        algo = (request.args.get("algo") or "srgb").lower()
        try:
            n = int(request.args.get("n", 21))
        except ValueError:
            return jsonify({"error": "n must be an integer"}), 400

        # Dispatch
        if algo not in supported_algorithms():
            return (
                jsonify(
                    {
                        "error": f"unknown algorithm '{algo}'",
                        "supported": supported_algorithms(),
                    }
                ),
                400,
            )
        try:
            if algo == "km_sub":
                palette = mix_km(a, b, n)
            elif algo == "hct_tone":
                palette = mix_hct_tone(
                    a,
                    n,
                    schedule=(request.args.get("schedule") or "linear").lower(),
                    gamma=float(request.args.get("gamma", 1.35)),
                )
            else:
                space, defaults = SPACE_MAP[algo]
                method = parse_method(request.args.get("method"))
                hue = parse_hue(request.args.get("hue"), defaults.get("hue"))
                palette = mix_steps(a, b, space=space, method=method, hue=hue, n=n)
        except Exception as exc:
            log.exception("Interpolation failed")
            return jsonify({"error": str(exc)}), 500

        return jsonify(palette)

    return app


if __name__ == "__main__":
    # Production: debug=False; threaded=True is fine for this I/O profile.
    create_app().run(debug=False, threaded=True)
