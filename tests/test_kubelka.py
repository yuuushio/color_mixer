import numpy as np
from color_mixer.kubelka import km_mix


def to_u8(rgb):
    return np.round(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)


def _hex(rgb):
    u8 = np.round(np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    return f"#{u8[0]:02x}{u8[1]:02x}{u8[2]:02x}"


def rgb01_to_hex(rgb):
    u8 = np.round(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
    return f"#{u8[0]:02x}{u8[1]:02x}{u8[2]:02x}"


def test_boundaries_red_white():
    red = np.array([1, 0, 0], np.float32)
    white = np.array([1, 1, 1], np.float32)
    # assert np.allclose(km_mix(red, white, 0.0), red, atol=1e-6)
    # assert np.allclose(km_mix(red, white, 1.0), white, atol=1e-6)
    assert np.array_equal(to_u8(km_mix(red, white, 0.0)), to_u8(red))
    assert np.array_equal(to_u8(km_mix(red, white, 1.0)), to_u8(white))


def test_boundaries_red_white():
    red = np.array([1, 0, 0], np.float32)
    white = np.array([1, 1, 1], np.float32)
    assert rgb01_to_hex(km_mix(red, white, 0.0)) == "#ff0000"
    assert rgb01_to_hex(km_mix(red, white, 1.0)) == "#ffffff"


def test_symmetry():
    a = np.array([0.2, 0.7, 0.1], np.float32)
    b = np.array([0.9, 0.1, 0.3], np.float32)
    t = 0.37
    ab = km_mix(a, b, t)
    ba = km_mix(b, a, 1 - t)
    assert np.allclose(ab, ba, atol=1e-6)


def test_monotonic_luminance_with_white():
    # Y' proxy: simple luma; we only need monotonic tendency
    w = np.array([1, 1, 1], np.float32)
    r = np.array([1, 0, 0], np.float32)
    seq = [km_mix(r, w, t) for t in np.linspace(0, 1, 11)]
    luma = [0.2126 * s[0] + 0.7152 * s[1] + 0.0722 * s[2] for s in seq]
    assert all(luma[i] <= luma[i + 1] + 1e-6 for i in range(len(luma) - 1))


def test_channel_bounds():
    a = np.array([0.05, 0.2, 0.9], np.float32)
    b = np.array([0.8, 0.7, 0.1], np.float32)
    for t in np.linspace(0, 1, 9):
        rgb = km_mix(a, b, t)
        assert np.all((rgb >= -1e-6) & (rgb <= 1 + 1e-6))
