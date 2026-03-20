"""Improved Gemini visible watermark remover with alpha gain search,
multi-pass removal, sub-pixel alignment, and noise floor handling."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import shift as ndshift

from .config import ASSETS_DIR

# --- constants ---
ALPHA_THRESHOLD = 0.002
MAX_ALPHA = 0.99
LOGO_VALUE = 255
ALPHA_NOISE_FLOOR = 3.0 / 255.0  # suppress quantisation noise from bg PNGs

# alpha-gain candidates: coarse search, then refined ±0.05 in 0.01 steps
_COARSE_GAINS = [1.0, 1.05, 1.12, 1.20, 1.28, 1.36, 1.45, 1.55, 1.70, 1.85, 2.0]

# position jitter (pixels) to search around expected placement
_POS_JITTER = [-4, -2, -1, 0, 1, 2, 4]

# sub-pixel shifts for fine alignment
_SUBPX_SHIFTS = [-0.25, 0.0, 0.25]

MAX_PASSES = 3
NEAR_BLACK_THRESHOLD = 5
NEAR_BLACK_INCREASE_LIMIT = 0.05


# --- alpha map ---

@lru_cache(maxsize=4)
def _load_alpha_map(size: int) -> np.ndarray:
    """Load reference bg image → float32 alpha map."""
    path = ASSETS_DIR / f"bg_{size}.png"
    img = Image.open(path).convert("RGB")
    arr = np.asarray(img, dtype=np.float32)
    alpha_map = arr.max(axis=2) / 255.0
    return alpha_map


def _interpolate_alpha(target_size: int) -> np.ndarray:
    """Get alpha map for any size by bilinear interpolation from 96px base."""
    base = _load_alpha_map(96)
    if target_size == 96:
        return base
    if target_size == 48:
        return _load_alpha_map(48)
    from PIL import Image as _Img

    base_img = _Img.fromarray((base * 255).astype(np.uint8), "L")
    resized = base_img.resize((target_size, target_size), _Img.BILINEAR)
    return np.asarray(resized, dtype=np.float32) / 255.0


def _shift_alpha(alpha: np.ndarray, dy: float, dx: float) -> np.ndarray:
    """Shift alpha map by fractional pixels using scipy."""
    if dy == 0.0 and dx == 0.0:
        return alpha
    return ndshift(alpha, [dy, dx], order=1, mode="constant", cval=0.0)


# --- watermark geometry ---

def detect_watermark_config(width: int, height: int) -> dict:
    if width > 1024 and height > 1024:
        return {"logo_size": 96, "margin_right": 64, "margin_bottom": 64}
    return {"logo_size": 48, "margin_right": 32, "margin_bottom": 32}


def calculate_watermark_position(width: int, height: int, config: dict) -> dict:
    logo = config["logo_size"]
    return {
        "x": width - config["margin_right"] - logo,
        "y": height - config["margin_bottom"] - logo,
        "w": logo,
        "h": logo,
    }


# --- scoring ---

def _spatial_score(region: np.ndarray, alpha: np.ndarray) -> float:
    """Normalised cross-correlation between greyscale region and alpha map."""
    grey = region.mean(axis=2) if region.ndim == 3 else region.astype(np.float32)
    g = grey.ravel().astype(np.float64)
    a = alpha.ravel().astype(np.float64)
    g = g - g.mean()
    a = a - a.mean()
    denom = np.sqrt((g * g).sum() * (a * a).sum())
    if denom < 1e-12:
        return 0.0
    return float(np.dot(g, a) / denom)


def _near_black_ratio(arr: np.ndarray) -> float:
    return float((arr.max(axis=2) <= NEAR_BLACK_THRESHOLD).mean())


# --- single-pass reverse blend ---

def _reverse_blend(arr: np.ndarray, alpha: np.ndarray, gain: float = 1.0) -> np.ndarray:
    """Apply reverse alpha blending with given gain."""
    # denoise: use noise-floor-subtracted alpha for activation mask
    signal_alpha = np.maximum(alpha * gain - ALPHA_NOISE_FLOOR, 0.0)
    mask = signal_alpha >= ALPHA_THRESHOLD

    # use raw (gained) alpha for the actual inverse solve
    raw = np.minimum(alpha * gain, MAX_ALPHA)
    a3 = raw[:, :, np.newaxis]
    om3 = (1.0 - raw)[:, :, np.newaxis]

    restored = (arr - a3 * LOGO_VALUE) / om3
    restored = np.clip(restored, 0, 255).astype(np.uint8)

    original = arr.astype(np.uint8)
    mask3 = np.stack([mask] * 3, axis=2)
    return np.where(mask3, restored, original)


# --- gain search ---

def _find_best_gain(arr: np.ndarray, alpha: np.ndarray) -> float:
    """Search for the alpha gain that minimises spatial correlation after removal."""
    best_gain = 1.0
    best_score = 999.0

    for g in _COARSE_GAINS:
        result = _reverse_blend(arr, alpha, g)
        sc = abs(_spatial_score(result.astype(np.float32), alpha))
        if sc < best_score:
            best_score = sc
            best_gain = g

    # fine search around best coarse gain
    fine_start = max(0.9, best_gain - 0.05)
    fine_end = best_gain + 0.06
    for g in np.arange(fine_start, fine_end, 0.01):
        result = _reverse_blend(arr, alpha, g)
        sc = abs(_spatial_score(result.astype(np.float32), alpha))
        if sc < best_score:
            best_score = sc
            best_gain = float(g)

    return best_gain


# --- position search ---

def _find_best_position(
    image: Image.Image,
    config: dict,
    base_pos: dict,
) -> dict:
    """Try jittered positions; return the one with highest watermark correlation."""
    w_img, h_img = image.size
    logo = config["logo_size"]
    alpha = _interpolate_alpha(logo)
    best_pos = base_pos
    best_score = 0.0

    for jx in _POS_JITTER:
        for jy in _POS_JITTER:
            x = base_pos["x"] + jx
            y = base_pos["y"] + jy
            if x < 0 or y < 0 or x + logo > w_img or y + logo > h_img:
                continue
            region = np.asarray(
                image.crop((x, y, x + logo, y + logo)), dtype=np.float32
            )
            sc = abs(_spatial_score(region, alpha))
            if sc > best_score:
                best_score = sc
                best_pos = {"x": x, "y": y, "w": logo, "h": logo}

    return best_pos


# --- core removal ---

def remove_watermark(image: Image.Image) -> Image.Image:
    """Remove the Gemini watermark and return the cleaned image."""
    image = image.convert("RGB")
    w, h = image.size

    config = detect_watermark_config(w, h)
    base_pos = calculate_watermark_position(w, h, config)
    logo = config["logo_size"]
    alpha_base = _interpolate_alpha(logo)

    # 1. find best position (jitter search)
    pos = _find_best_position(image, config, base_pos)
    x, y, ww, hh = pos["x"], pos["y"], pos["w"], pos["h"]

    # 2. find best sub-pixel alignment (only if it improves score meaningfully)
    region_f = np.asarray(
        image.crop((x, y, x + ww, y + hh)), dtype=np.float32
    )
    best_alpha = alpha_base[:hh, :ww]
    best_sub_score = abs(_spatial_score(region_f, best_alpha))

    for sy in _SUBPX_SHIFTS:
        for sx in _SUBPX_SHIFTS:
            if sy == 0.0 and sx == 0.0:
                continue
            shifted = _shift_alpha(alpha_base, sy, sx)[:hh, :ww]
            sc = abs(_spatial_score(region_f, shifted))
            # require >2% improvement to avoid false alignment
            if sc > best_sub_score * 1.02:
                best_sub_score = sc
                best_alpha = shifted

    # 3. first pass with gain=1.0
    current = region_f.copy()
    initial_black = _near_black_ratio(current.astype(np.uint8))
    result = _reverse_blend(current, best_alpha, 1.0)
    current = result.astype(np.float32)
    residual = abs(_spatial_score(current, best_alpha))

    # 4. if significant residual remains, try gain search + multi-pass
    if residual >= 0.25:
        # reset and find better gain
        best_gain = _find_best_gain(region_f, best_alpha)
        current = region_f.copy()

        for _pass in range(MAX_PASSES):
            result = _reverse_blend(current, best_alpha, best_gain)

            # safety: check near-black increase
            black_ratio = _near_black_ratio(result)
            if black_ratio - initial_black > NEAR_BLACK_INCREASE_LIMIT:
                break

            current = result.astype(np.float32)

            # check if watermark trace is gone
            residual = abs(_spatial_score(current, best_alpha))
            if residual < 0.15:
                break

    # 5. patch result back
    patched = Image.fromarray(current.astype(np.uint8), "RGB")
    out = image.copy()
    out.paste(patched, (x, y))
    return out
