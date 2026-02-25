"""Port of gemini-watermark-remover's reverse alpha blending algorithm."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from .config import ASSETS_DIR

# --- constants (match upstream) ---
ALPHA_THRESHOLD = 0.002  # ignore noise-level alpha
MAX_ALPHA = 0.99  # cap to avoid division by near-zero
LOGO_VALUE = 255  # white watermark


# --- alpha map ---

@lru_cache(maxsize=2)
def _load_alpha_map(size: int) -> np.ndarray:
    """Load a reference bg image and derive a float32 alpha map.

    The reference images are the Gemini watermark rendered on a pure black
    background.  On black, alpha blending gives pixel = alpha * 255, so
    alpha = max(R,G,B) / 255.
    """
    path = ASSETS_DIR / f"bg_{size}.png"
    img = Image.open(path).convert("RGB")
    arr = np.asarray(img, dtype=np.float32)  # (H, W, 3)
    alpha_map = arr.max(axis=2) / 255.0  # (H, W)
    return alpha_map


# --- watermark geometry ---

def detect_watermark_config(width: int, height: int) -> dict:
    """Return logo_size, margin_right, margin_bottom for the given image size."""
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


# --- core removal ---

def remove_watermark(image: Image.Image) -> Image.Image:
    """Remove the Gemini watermark and return the cleaned image."""
    image = image.convert("RGB")
    w, h = image.size

    config = detect_watermark_config(w, h)
    pos = calculate_watermark_position(w, h, config)
    alpha_map = _load_alpha_map(config["logo_size"])

    # work on the watermark region only
    x, y, ww, hh = pos["x"], pos["y"], pos["w"], pos["h"]
    region = image.crop((x, y, x + ww, y + hh))
    arr = np.asarray(region, dtype=np.float32)  # (hh, ww, 3)

    alpha = alpha_map[:hh, :ww]  # should already match, but be safe
    mask = alpha >= ALPHA_THRESHOLD
    capped = np.minimum(alpha, MAX_ALPHA)
    one_minus = 1.0 - capped

    # broadcast alpha to (H, W, 1) for channel-wise math
    a3 = capped[:, :, np.newaxis]
    om3 = one_minus[:, :, np.newaxis]

    restored = (arr - a3 * LOGO_VALUE) / om3
    restored = np.clip(restored, 0, 255).astype(np.uint8)

    # only apply where alpha is significant
    original = np.asarray(region, dtype=np.uint8)
    mask3 = np.stack([mask] * 3, axis=2)
    result = np.where(mask3, restored, original)

    patched = Image.fromarray(result, "RGB")
    out = image.copy()
    out.paste(patched, (x, y))
    return out
