# kubelka.py – faithful port of Ronald van Wijnen’s 38-sample KM mixer

import numpy as np
import logging
from colour.colorimetry import MSDS_CMFS, SpectralShape
from colour.models import RGB_COLOURSPACE_sRGB

log = logging.getLogger(__name__)
_GAMMA = 2.4
_DL = 10.0  # nm step

# ── 1.  Van Wijnen 7-basis spectra (already in the repo) ────────────────────
from .spectra38 import BASE_SPECTRA  # dict with keys W,C,M,Y,R,G,B

ORDER = ("W", "C", "M", "Y", "R", "G", "B")
_BASE = np.stack([BASE_SPECTRA[k] for k in ORDER])  # 7 × 38

# ── 2.  CIE CMFs (38 samples, 380–750 nm) ───────────────────────────────────
shape = SpectralShape(380, 750, 10)
_CMF = (
    MSDS_CMFS["CIE 1931 2 Degree Standard Observer"]
    .copy()
    .align(shape)
    .values.T.astype(np.float32)
)  # 3 × 38
k_Y = 100.0 / (_CMF[1] * _DL).sum()

# ── 3.  sRGB ↔ XYZ matrices (colour-science) ────────────────────────────────
_RGB_XYZ = RGB_COLOURSPACE_sRGB.matrix_RGB_to_XYZ.astype(np.float32)
_XYZ_RGB = RGB_COLOURSPACE_sRGB.matrix_XYZ_to_RGB.astype(np.float32)


# ── 4.  helper transfer curves ─────────────────────────────────────────────
def _uncompand(v):
    v = np.asarray(v, np.float32)
    m = v > 0.04045
    out = np.empty_like(v)
    out[m] = ((v[m] + 0.055) / 1.055) ** _GAMMA
    out[~m] = v[~m] / 12.92
    return out


def _compand(v):
    v = np.asarray(v, np.float32)
    m = v > 0.0031308
    out = np.empty_like(v)
    out[m] = 1.055 * np.power(v[m], 1 / _GAMMA) - 0.055
    out[~m] = v[~m] * 12.92
    return out


_ks = lambda R: (1 - R) ** 2 / (2 * R)
_km = lambda ks: 1 + ks - np.sqrt(ks * ks + 2 * ks)


# ── 5.  companded-sRGB ➜ 38-sample reflectance (closed-form) ───────────────
def _srgb_to_R(rgb_srgb):
    """Companded sRGB in [0,1] ➜ Kubelka–Munk reflectance (38 samples)."""
    lrgb = _uncompand(rgb_srgb)

    w = lrgb.min()
    lrgb -= w

    c = min(lrgb[1], lrgb[2])
    m = min(lrgb[0], lrgb[2])
    y = min(lrgb[0], lrgb[1])

    r = max(0, min(lrgb[0] - lrgb[2], lrgb[0] - lrgb[1]))
    g = max(0, min(lrgb[1] - lrgb[2], lrgb[1] - lrgb[0]))
    b = max(0, min(lrgb[2] - lrgb[1], lrgb[2] - lrgb[0]))

    coeffs = np.array([w, c, m, y, r, g, b], np.float32)[:, None]  # 7×1
    R = (_BASE * coeffs).sum(axis=0)
    return np.clip(R, 1e-6, 1.0)


# ── 6.  reflectance ➜ companded-sRGB ───────────────────────────────────────
def _R_to_srgb(R):
    XYZ = k_Y * (_CMF * _DL) @ R
    lrgb = (_XYZ_RGB @ (XYZ / 100.0)).clip(0, 1)
    return _compand(lrgb)


# ───  helper: Y (relative luminance 0‒1) ──────────────────────────────────
def _luminance(R: np.ndarray) -> float:
    # Y of reflectance under D65, scaled to [0‒1]
    return float(((_CMF[1] * _DL) @ R) * k_Y / 100.0)


# ───  public mixer  ────────────────────────────────────────────────────────
def km_mix(rgb_a: np.ndarray, rgb_b: np.ndarray, t: float) -> np.ndarray:
    """
    Kubelka–Munk subtractive mix with concentration weighting
    identical to Ronald van Wijnen’s spectral.js (MIT 2025).
    """
    # 1) spectra & K/S
    Ra, Rb = _srgb_to_R(rgb_a), _srgb_to_R(rgb_b)
    ksA, ksB = _ks(Ra), _ks(Rb)

    # 2) per-colour concentration
    concA = (1.0 - t) ** 2 * (_luminance(Ra) ** 0.5)
    concB = t**2 * (_luminance(Rb) ** 0.5)
    total = concA + concB or 1.0  # avoid divide-by-zero

    # 3) weighted average in K/S space
    ks_mix = (ksA * concA + ksB * concB) / total
    R_mix = _km(ks_mix)

    return _R_to_srgb(R_mix)
