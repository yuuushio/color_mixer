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


def tone_steps(
    n: int, *, schedule: Schedule = "ease", gamma: float = 1.35
) -> List[Tone]:
    """
    Generate n tone values in [100..0] inclusive from a density schedule.
      linear     – uniform steps
      ease       – cosine ease-in/out (default), denser near 100 and 0
      shadow     – concentrate steps toward dark tones
      highlight  – concentrate steps toward light tones
    """
    if n < 2:
        raise ValueError("n must be ≥ 2")

    out: List[Tone] = []
    g = max(1.001, float(gamma))
    for i in range(n):
        u = i / (n - 1)  # 0..1
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
        if i == 0:
            T = 100.0
        elif i == n - 1:
            T = 0.0
        out.append(T)
    return out


@dataclass
class HCTTonal:
    gamut: str = "srgb"
    schedule: Schedule = "ease"
    gamma: float = 1.35

    def ramp(
        self, seed_hex: Hex, n: int, *, schedule: Schedule | None = None
    ) -> List[Hex]:
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
