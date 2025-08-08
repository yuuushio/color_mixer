# kubelka.py – port of Ronald van Wijnen’s 38-sample KM mixer (MIT)
#   - 7 basis spectra (W,C,M,Y,R,G,B)
#   - sRGB companding with gamma 2.4 and IEC thresholds
#   - D65-weighted CIE 1931 2° CMFs for reflectance→XYZ integration
#   - Linear interpolation in K/S space (no extra concentration weighting)

from __future__ import annotations
import logging
import numpy as np
from colour.colorimetry import MSDS_CMFS, SDS_ILLUMINANTS, SpectralShape
from colour.models import RGB_COLOURSPACE_sRGB

log = logging.getLogger(__name__)

# --- constants ---------------------------------------------------------------
_GAMMA = 2.4
_DL = 10.0  # nm step, 380…750 inclusive → 38 samples

# --- 1) basis spectra W,C,M,Y,R,G,B (38×) -----------------------------------
# Keep this import as-is: your spectra38.py holds the exact arrays
from .spectra38 import BASE_SPECTRA  # dict with keys W,C,M,Y,R,G,B

_ORDER = ("W", "C", "M", "Y", "R", "G", "B")
_BASE = np.stack([BASE_SPECTRA[k] for k in _ORDER]).astype(np.float32)  # 7×38

# --- 2) D65-weighted CMFs (3×38) --------------------------------------------
# spectral.js uses CMFs already multiplied by D65 SPD; we replicate that.
shape = SpectralShape(380, 750, 10)
cmf = (
    MSDS_CMFS["CIE 1931 2 Degree Standard Observer"]
    .copy()
    .align(shape)
    .values.T.astype(np.float32)  # 3×38 (x̄, ȳ, z̄)
)
d65 = SDS_ILLUMINANTS["D65"].copy().align(shape).values.astype(np.float32)  # 38
_CMF = cmf * d65[None, :]  # weight each wavelength by D65
# CIE normalization so that a perfect diffuser R(λ)=1 gives Y = 100
k_Y = 100.0 / (_CMF[1] * _DL).sum()

# --- 3) sRGB↔XYZ matrices (D65) ---------------------------------------------
_RGB_XYZ = RGB_COLOURSPACE_sRGB.matrix_RGB_to_XYZ.astype(np.float32)
_XYZ_RGB = RGB_COLOURSPACE_sRGB.matrix_XYZ_to_RGB.astype(np.float32)


# Y (relative luminance 0–1)
def _luminance(R: np.ndarray) -> float:
    # Y = k_Y * ∑ ȳ(λ) * R(λ) * Δλ, scaled to [0–1]
    return float(((_CMF[1] * _DL) @ R) * k_Y / 100.0)


# --- 4) IEC 61966-2-1 companding --------------------------------------------
def _uncompand(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, np.float32)
    m = v > 0.04045
    out = np.empty_like(v)
    out[m] = ((v[m] + 0.055) / 1.055) ** _GAMMA
    out[~m] = v[~m] / 12.92
    return out


def _compand(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, np.float32)
    m = v > 0.0031308
    out = np.empty_like(v)
    out[m] = 1.055 * np.power(v[m], 1 / _GAMMA) - 0.055
    out[~m] = v[~m] * 12.92
    return out


# --- 5) KM transforms --------------------------------------------------------
def _ks(r: np.ndarray) -> np.ndarray:
    # K/S = (1 - R)^2 / (2R)
    r = np.clip(r, 1e-9, 1.0)
    return (1.0 - r) ** 2 / (2.0 * r)


def _km(ks: np.ndarray) -> np.ndarray:
    # invert K/S → R
    ks = np.maximum(ks, 0.0)
    return 1.0 + ks - np.sqrt(ks * ks + 2.0 * ks)


# --- 6) sRGB (companded) → reflectance (38×) --------------------------------
def _srgb_to_R(rgb_srgb: np.ndarray) -> np.ndarray:
    """Companded sRGB in [0,1] → reflectance spectrum (38 samples)."""
    lrgb = _uncompand(rgb_srgb).astype(np.float32)

    # spectral.js decomposition into W + C M Y R G B
    w = float(lrgb.min())
    lrgb = lrgb - w

    c = float(min(lrgb[1], lrgb[2]))
    m = float(min(lrgb[0], lrgb[2]))
    y = float(min(lrgb[0], lrgb[1]))

    r = float(max(0.0, min(lrgb[0] - lrgb[2], lrgb[0] - lrgb[1])))
    g = float(max(0.0, min(lrgb[1] - lrgb[2], lrgb[1] - lrgb[0])))
    b = float(max(0.0, min(lrgb[2] - lrgb[1], lrgb[2] - lrgb[0])))

    coeffs = np.array([w, c, m, y, r, g, b], dtype=np.float32)[:, None]  # 7×1
    R = (_BASE * coeffs).sum(axis=0)
    return np.clip(R, 1e-6, 1.0)


# --- 7) reflectance (38×) → sRGB (companded) --------------------------------
def _R_to_srgb(R: np.ndarray) -> np.ndarray:
    XYZ = k_Y * (_CMF * _DL) @ R  # absolute XYZ (Y≈0..100)
    lrgb = (_XYZ_RGB @ (XYZ / 100.0)).astype(np.float32)
    lrgb = np.clip(lrgb, 0.0, 1.0)
    return _compand(lrgb)


# --- 8) public mixer ---------------------------------------------------------
def km_mix(rgb_a: np.ndarray, rgb_b: np.ndarray, t: float) -> np.ndarray:
    Ra, Rb = _srgb_to_R(rgb_a), _srgb_to_R(rgb_b)
    ksA, ksB = _ks(Ra), _ks(Rb)

    # concentration weights — match spectral.js
    concA = (1.0 - t) ** 2 * _luminance(Ra)  # no sqrt
    concB = t**2 * _luminance(Rb)  # no sqrt
    total = concA + concB or 1.0

    ks_mix = (ksA * concA + ksB * concB) / total
    return _R_to_srgb(_km(ks_mix))
