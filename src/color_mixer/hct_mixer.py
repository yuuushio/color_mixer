# hct_mixer.py

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import asinh, sinh
from typing import Callable

from coloraide import Color

# Ensure HCT is registered (safe if already present).
try:
    from coloraide.spaces.hct import HCT  # type: ignore[attr-defined]

    Color.register(HCT())
except Exception:
    pass

Hex = str
Hue = float
Chroma = float
Tone = float

WarpFn = Callable[[float], float]  # hue → warped hue (deg)
UnwarpFn = Callable[[float], float]  # warped hue → hue (deg)


def _short_arc_lerp(h1: Hue, h2: Hue, t: float) -> Hue:
    d = ((h2 - h1 + 180.0) % 360.0) - 180.0
    return (h1 + t * d) % 360.0


@dataclass(frozen=True)
class SatMapper:
    k: float = 0.60

    def __post_init__(self) -> None:
        object.__setattr__(self, "_norm", asinh(1.0 / self.k))

    def encode(self, c: Chroma, cmax: Chroma) -> float:
        if cmax <= 0.0:
            return 0.0
        return asinh((max(0.0, c) / cmax) / self.k) / self._norm  # type: ignore[attr-defined]

    def decode(self, s: float, cmax: Chroma) -> Chroma:
        s1 = 0.0 if s <= 0.0 else 1.0 if s >= 1.0 else s
        return cmax * self.k * sinh(s1 * self._norm)  # type: ignore[attr-defined]


@lru_cache(maxsize=16384)
def _cmax_cached(
    gamut: str, cmax_hi: float, cmax_iters: int, hq: Hue, tq: Tone
) -> Chroma:
    """
    Max chroma for quantized (h, t) in a target gamut.
    Cached at module level so it doesn't depend on a hashable instance.
    """
    hi = float(cmax_hi)
    if Color("hct", [hq, hi, tq]).convert(gamut).in_gamut():
        return hi
    lo = 0.0
    iters = max(1, int(cmax_iters))
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if Color("hct", [hq, mid, tq]).convert(gamut).in_gamut():
            lo = mid
        else:
            hi = mid
    return lo


@dataclass
class HCTMix:
    gamut: str = "srgb"
    clamp_tones: tuple[Tone, Tone] = (2.0, 98.0)
    k_sat: float = 0.60
    s_freeze: float = 0.02
    cmax_hi: float = 120.0
    cmax_iters: int = 8
    quant_h: float = 0.25
    quant_t: float = 0.25
    hue_warp: WarpFn = staticmethod(lambda h: h)
    hue_unwarp: UnwarpFn = staticmethod(lambda H: H)

    def mix(self, a_hex: Hex, b_hex: Hex, n: int) -> list[Hex]:
        if n < 2:
            raise ValueError("n must be ≥ 2")

        A = Color(a_hex)
        B = Color(b_hex)
        a_h, a_c, a_t = self._to_hct(A)
        b_h, b_c, b_t = self._to_hct(B)

        t0 = self._clamp_t(a_t)
        t1 = self._clamp_t(b_t)

        sat_map = SatMapper(self.k_sat)

        ca_max = self._cmax(a_h, t0)
        cb_max = self._cmax(b_h, t1)
        sa = sat_map.encode(a_c, ca_max)
        sb = sat_map.encode(b_c, cb_max)

        out: list[Hex] = []
        for i in range(n):
            t = i / (n - 1)

            Ti: Tone = t0 + (t1 - t0) * t

            Ha = self.hue_warp(a_h)
            Hb = self.hue_warp(b_h)
            Hi = _short_arc_lerp(Ha, Hb, t)
            hi = self.hue_unwarp(Hi)

            c_env = self._cmax(hi, Ti)

            si = sa + (sb - sa) * t
            if si < self.s_freeze:
                hi = a_h if t < 0.5 else b_h
                c_env = self._cmax(hi, Ti)

            ci = sat_map.decode(si, c_env)

            hex_i = (
                Color("hct", [hi, ci, Ti])
                .convert(self.gamut)
                .to_string(hex=True, fit={"method": "raytrace", "pspace": "hct"})
            )
            out.append(hex_i)

        out[0] = A.convert(self.gamut).to_string(
            hex=True, fit={"method": "raytrace", "pspace": "hct"}
        )
        out[-1] = B.convert(self.gamut).to_string(
            hex=True, fit={"method": "raytrace", "pspace": "hct"}
        )
        return out

    # ---- internals ----

    def _to_hct(self, c: Color) -> tuple[Hue, Chroma, Tone]:
        hct = c.convert("hct")
        h = float(hct["h"]) % 360.0
        return h, float(hct["c"]), float(hct["t"])

    def _clamp_t(self, t: Tone) -> Tone:
        lo, hi = self.clamp_tones
        return hi if t > hi else lo if t < lo else t

    def _cmax(self, h: Hue, t: Tone) -> Chroma:
        qh = round(h / self.quant_h) * self.quant_h
        qt = round(t / self.quant_t) * self.quant_t
        return _cmax_cached(
            self.gamut, float(self.cmax_hi), int(self.cmax_iters), qh, qt
        )


def mix_hct(
    a_hex: Hex,
    b_hex: Hex,
    n: int,
    *,
    gamut: str = "srgb",
    clamp_tones: tuple[Tone, Tone] = (2.0, 98.0),
    k_sat: float = 0.60,
    s_freeze: float = 0.02,
    cmax_hi: float = 120.0,
    cmax_iters: int = 8,
    quant_h: float = 0.25,
    quant_t: float = 0.25,
    hue_warp: WarpFn = lambda h: h,
    hue_unwarp: UnwarpFn = lambda H: H,
) -> list[Hex]:
    return HCTMix(
        gamut=gamut,
        clamp_tones=clamp_tones,
        k_sat=k_sat,
        s_freeze=s_freeze,
        cmax_hi=cmax_hi,
        cmax_iters=cmax_iters,
        quant_h=quant_h,
        quant_t=quant_t,
        hue_warp=hue_warp,
        hue_unwarp=hue_unwarp,
    ).mix(a_hex, b_hex, n)


__all__ = ["HCTMix", "mix_hct"]

if __name__ == "__main__":
    demo = mix_hct("#005457", "#fa7a76", 19)
    print(demo)
