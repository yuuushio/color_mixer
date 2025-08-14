from __future__ import annotations
from dataclasses import dataclass
from math import cos, pi
from typing import List, Literal

from coloraide import Color

# Ensure HCT is registered (idempotent).
try:
    from coloraide.spaces.hct import HCT  # type: ignore[attr-defined]

    Color.register(HCT())
except Exception:
    pass

Hex = str
Tone = float
Schedule = Literal["linear", "ease", "shadow", "highlight"]


def _canon_hex(s: str) -> Hex:
    s = s.strip()
    if not s:
        raise ValueError("empty color")
    if s[0] != "#":
        s = "#" + s
    raw = s[1:]
    if len(raw) == 3 and all(c in "0123456789abcdefABCDEF" for c in raw):
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6 or not all(c in "0123456789abcdefABCDEF" for c in raw):
        raise ValueError(f"invalid hex: {s}")
    return "#" + raw.lower()


TONE_QUANT = 1e-4  # 4 decimal places; safe for n <= 512 with schedule="ease"


def _q(x: float, q: float = TONE_QUANT) -> float:
    # clamp to [0,100] and quantize
    return max(0.0, min(100.0, round(x / q) * q))


def tone_steps(
    n: int, *, schedule: Schedule = "ease", gamma: float = 1.35
) -> List[Tone]:
    """
    Generate n tone values in [100..0] inclusive.
    IMPORTANT: n counts the endpoints; the schedule only distributes the n-2 interior tones.
      linear     – uniform interior spacing
      ease       – cosine ease-in/out (default), denser near 100 and 0
      shadow     – concentrate interior samples toward dark tones
      highlight  – concentrate interior samples toward light tones
    """
    if n < 3:
        raise ValueError("n must be ≥ 3 (includes tone 100 and 0 plus ≥1 interior)")

    out: List[Tone] = [100.0]  # first endpoint
    g = max(1.001, float(gamma))

    # interior samples: j = 1..n-2 ; normalize to 0<u<1 using n-1 as the denominator
    for j in range(1, n - 1):
        u = j / (n - 1)  # 0 < u < 1
        if schedule == "linear":
            v = u
        elif schedule == "ease":
            v = 0.5 - 0.5 * cos(pi * u)
        elif schedule == "shadow":
            v = u**g
        elif schedule == "highlight":
            v = 1.0 - (1.0 - u) ** g
        else:
            raise ValueError(f"unknown schedule '{schedule}'")
        T = 100.0 * (1.0 - v)
        out.append(_q(T))

    out.append(0.0)  # last endpoint
    return out


@dataclass
class HCTTonal:
    gamut: str = "srgb"
    schedule: Schedule = "ease"
    gamma: float = 1.35

    def ramp(
        self, seed_hex: Hex, n: int, *, schedule: Schedule | None = None
    ) -> List[Hex]:
        if n < 3:
            raise ValueError("n must be ≥ 3 for HCT tonal ramps")
        seed = Color(_canon_hex(seed_hex)).convert("hct")
        tones = tone_steps(n, schedule=schedule or self.schedule, gamma=self.gamma)
        out: List[Hex] = []
        for T in tones:
            hex_i = (
                seed.clone()
                .set("t", float(T))
                .convert(self.gamut)
                .to_string(hex=True, fit={"method": "raytrace", "pspace": "hct"})
            )
            out.append(hex_i)
        return out


def tonal_ramp(
    seed_hex: Hex,
    n: int,
    *,
    schedule: Schedule = "ease",
    gamut: str = "srgb",
    gamma: float = 1.35,
) -> List[Hex]:
    return HCTTonal(gamut=gamut, schedule=schedule, gamma=gamma).ramp(seed_hex, n)


__all__ = ["HCTTonal", "tonal_ramp", "tone_steps"]
