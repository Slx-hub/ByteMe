"""Seamless Floyd-Steinberg dithering onto the panel palette.

Two things make this different from a generic image editor's dither:

1. It runs as a single serial pass over the whole image. Paint.NET (and most
   editors) dither tiles in parallel threads, so the error never crosses a tile
   boundary and you get visible seams. Here the error propagates from the first
   pixel to the last, so there are no chunk edges.

2. It can thin out green. The panel over-renders green: any area containing
   green pixels reads as green-dominant. `green_reduce` is the probability that
   a pixel which *would* have been green is pushed to its second-nearest palette
   colour instead. The residual error still diffuses normally, so the image
   holds together and the green density falls smoothly with the slider.
"""

import numpy as np

from .palette import PALETTE, GREEN_INDEX

# Nearest-colour lookup is precomputed on a quantised RGB grid. 6 bits per
# channel (262144 entries) keeps the choice effectively exact while turning the
# inner loop into a single array index.
_BITS = 6
_LEVELS = 1 << _BITS
_SHIFT = 8 - _BITS

_lut_best = None
_lut_second = None


def _build_luts():
    global _lut_best, _lut_second
    if _lut_best is not None:
        return
    axis = (np.arange(_LEVELS, dtype=np.float32) + 0.5) * (256.0 / _LEVELS)
    grid = np.stack(np.meshgrid(axis, axis, axis, indexing='ij'), -1).reshape(-1, 3)
    delta = grid[:, None, :] - PALETTE[None, :, :]
    order = np.argsort((delta * delta).sum(2), axis=1)
    _lut_best = order[:, 0].astype(np.uint8)
    _lut_second = order[:, 1].astype(np.uint8)


def dither(rgb, green_reduce=0.0, seed=0x9E3779B9):
    """Dither an (h, w, 3) uint8 array to (h, w) uint8 palette indices.

    green_reduce: 0.0 keeps every green pixel, 1.0 removes essentially all of them.
    seed:         fixed so a given image and settings always render identically.
    """
    _build_luts()

    height, width, _ = rgb.shape
    buf = rgb.astype(np.float32)
    out = np.empty((height, width), dtype=np.uint8)

    # Scalar Python in the hot loop is markedly faster than per-pixel numpy.
    pal_r = PALETTE[:, 0].tolist()
    pal_g = PALETTE[:, 1].tolist()
    pal_b = PALETTE[:, 2].tolist()
    lut_best = _lut_best.tolist()
    lut_second = _lut_second.tolist()

    suppress = green_reduce > 0.0
    if suppress:
        noise = np.random.default_rng(seed).random((height, width), dtype=np.float32)

    for y in range(height):
        row = buf[y].tolist()
        next_row = buf[y + 1].tolist() if y + 1 < height else None
        row_noise = noise[y].tolist() if suppress else None
        out_row = [0] * width

        for x in range(width):
            pixel = row[x]
            r = pixel[0]
            g = pixel[1]
            b = pixel[2]

            ri = 0 if r < 0.0 else (255 if r > 255.0 else int(r))
            gi = 0 if g < 0.0 else (255 if g > 255.0 else int(g))
            bi = 0 if b < 0.0 else (255 if b > 255.0 else int(b))
            key = ((ri >> _SHIFT) << (2 * _BITS)) | ((gi >> _SHIFT) << _BITS) | (bi >> _SHIFT)

            index = lut_best[key]
            if suppress and index == GREEN_INDEX and row_noise[x] < green_reduce:
                index = lut_second[key]
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


def quantize_no_dither(rgb):
    """Nearest palette colour per pixel, no error diffusion. Used for flat art."""
    delta = rgb[:, :, None, :].astype(np.int32) - PALETTE[None, None, :, :].astype(np.int32)
    return (delta * delta).sum(3).argmin(2).astype(np.uint8)


def colour_histogram(indices):
    """Fraction of the image occupied by each palette colour."""
    counts = np.bincount(indices.ravel(), minlength=len(PALETTE))
    return (counts / indices.size).tolist()
