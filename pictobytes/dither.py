"""Seamless Floyd-Steinberg dithering onto the panel palette.

Two things make this different from a generic image editor's dither:

1. It runs as a single serial pass over the whole image. Paint.NET (and most
   editors) dither tiles in parallel threads, so the error never crosses a tile
   boundary and you get visible seams. Here the error propagates from the first
   pixel to the last, so there are no chunk edges.

2. It can thin out green. The panel over-renders green: an area containing
   green pixels reads as green-dominant, and because the nominal green
   (#527743) is a dark desaturated olive, the matcher also reaches for it to
   represent ordinary midtones and shadows.

   `green_reduce` fixes both by telling the matcher that green *looks* more
   saturated than its nominal value. Green then stops winning for neutral
   midtones, and where the image really is green each pixel "counts for more",
   so fewer are needed. The error diffuses against the same adjusted colour, so
   the surrounding pixels compensate coherently.

   Substituting individual green pixels for their runner-up does not work: the
   green-biased error left behind diffuses outward, turns the neighbours green,
   and spreads a green speckle across the whole image at lower density. Counting
   green pixels says it improved; looking at it says otherwise.
"""

import colorsys

import numpy as np

from .palette import PALETTE, GREEN_INDEX

# Nearest-colour lookup is precomputed on a quantised RGB grid. 6 bits per
# channel (262144 entries) keeps the choice effectively exact while turning the
# inner loop into a single array index.
_BITS = 6
_LEVELS = 1 << _BITS
_SHIFT = 8 - _BITS

# Fully saturated version of the panel's green. green_reduce slides the green
# the matcher sees from its nominal value towards this, which is what makes the
# dither spend fewer pixels on it.
_h, _s, _v = colorsys.rgb_to_hsv(*(PALETTE[GREEN_INDEX] / 255.0))
VIVID_GREEN = np.array(colorsys.hsv_to_rgb(_h, 1.0, 1.0), dtype=np.float32) * 255.0

# LUTs are cached per green_reduce step; the slider only has so many positions.
_LUT_STEPS = 50
_lut_cache = {}


def match_palette(green_reduce):
    """The palette the matcher uses. Output indices are unchanged."""
    palette = PALETTE.copy()
    if green_reduce > 0.0:
        palette[GREEN_INDEX] += green_reduce * (VIVID_GREEN - PALETTE[GREEN_INDEX])
    return palette


def _build_lut(green_reduce):
    key = round(green_reduce * _LUT_STEPS)
    cached = _lut_cache.get(key)
    if cached is not None:
        return cached

    palette = match_palette(key / _LUT_STEPS)
    axis = (np.arange(_LEVELS, dtype=np.float32) + 0.5) * (256.0 / _LEVELS)
    grid = np.stack(np.meshgrid(axis, axis, axis, indexing='ij'), -1).reshape(-1, 3)
    delta = grid[:, None, :] - palette[None, :, :]
    lut = (delta * delta).sum(2).argmin(1).astype(np.uint8)

    _lut_cache[key] = (lut, palette)
    return lut, palette


def dither(rgb, green_reduce=0.0):
    """Dither an (h, w, 3) uint8 array to (h, w) uint8 palette indices.

    green_reduce: 0.0 uses the palette as-is, 1.0 matches against a fully
    saturated green and so spends the fewest pixels on it. Deterministic.
    """
    lut, palette = _build_lut(green_reduce)

    height, width, _ = rgb.shape
    buf = rgb.astype(np.float32)
    out = np.empty((height, width), dtype=np.uint8)

    # Scalar Python in the hot loop is markedly faster than per-pixel numpy.
    # The error is measured against the same palette the matcher used, which is
    # what keeps the compensation coherent instead of speckled.
    pal_r = palette[:, 0].tolist()
    pal_g = palette[:, 1].tolist()
    pal_b = palette[:, 2].tolist()
    lut_best = lut.tolist()

    for y in range(height):
        row = buf[y].tolist()
        next_row = buf[y + 1].tolist() if y + 1 < height else None
        out_row = [0] * width

        for x in range(width):
            pixel = row[x]
            # Clamp to the displayable range *before* measuring the error, not
            # just before the palette lookup. A colour the palette cannot reach
            # -- a saturated cyan sky, say, against a palette with no cyan --
            # leaves residual error every pixel. Carried unclamped that
            # compounds instead of saturating: working values reached -5844 on
            # an 800x480 photo, and the flood of error starved black, red and
            # orange out of the whole image. Error that would push a pixel
            # outside 0..255 is not recoverable by any later pixel anyway.
            r = 0.0 if pixel[0] < 0.0 else (255.0 if pixel[0] > 255.0 else pixel[0])
            g = 0.0 if pixel[1] < 0.0 else (255.0 if pixel[1] > 255.0 else pixel[1])
            b = 0.0 if pixel[2] < 0.0 else (255.0 if pixel[2] > 255.0 else pixel[2])

            key = ((int(r) >> _SHIFT) << (2 * _BITS)) | \
                  ((int(g) >> _SHIFT) << _BITS) | (int(b) >> _SHIFT)

            index = lut_best[key]
            out_row[x] = index

            err_r = r - pal_r[index]
            err_g = g - pal_g[index]
            err_b = b - pal_b[index]

            if x + 1 < width:
                p = row[x + 1]
                p[0] += err_r * 0.4375
                p[1] += err_g * 0.4375
                p[2] += err_b * 0.4375
            if next_row is not None:
                if x:
                    p = next_row[x - 1]
                    p[0] += err_r * 0.1875
                    p[1] += err_g * 0.1875
                    p[2] += err_b * 0.1875
                p = next_row[x]
                p[0] += err_r * 0.3125
                p[1] += err_g * 0.3125
                p[2] += err_b * 0.3125
                if x + 1 < width:
                    p = next_row[x + 1]
                    p[0] += err_r * 0.0625
                    p[1] += err_g * 0.0625
                    p[2] += err_b * 0.0625

        if next_row is not None:
            buf[y + 1] = next_row
        out[y] = out_row

    return out


def quantize_no_dither(rgb, green_reduce=0.0):
    """Nearest palette colour per pixel, no error diffusion. Used for flat art."""
    palette = match_palette(green_reduce)
    delta = rgb[:, :, None, :].astype(np.float32) - palette[None, None, :, :]
    return (delta * delta).sum(3).argmin(2).astype(np.uint8)


def colour_histogram(indices):
    """Fraction of the image occupied by each palette colour."""
    counts = np.bincount(indices.ravel(), minlength=len(PALETTE))
    return (counts / indices.size).tolist()
