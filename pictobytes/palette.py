"""The 7 colours of the e-paper panel, in device index order."""

import numpy as np

# Index order must match the panel's colour indices (0..6) and the order
# used by GLaDOS' picture_frame_util.palette_colors.
#          0 Black    1 White    2 Green    3 Blue     4 Red      5 Yellow   6 Orange
PALETTE_HEX = ['282828', 'E5E5E5', '527743', '545CCE', 'A04E4E', 'FFFF47', 'A8663A']

PALETTE_NAMES = ['Black', 'White', 'Green', 'Blue', 'Red', 'Yellow', 'Orange']

GREEN_INDEX = 2

PALETTE = np.array([[int(h[i:i + 2], 16) for i in (0, 2, 4)] for h in PALETTE_HEX],
                   dtype=np.float32)

PALETTE_U8 = PALETTE.astype(np.uint8)

WIDTH = 800
HEIGHT = 480


def indices_to_rgb(indices):
    """(h, w) uint8 palette indices -> (h, w, 3) uint8 RGB."""
    return PALETTE_U8[indices]


def indices_to_glds(indices):
    """(h, w) uint8 palette indices -> .glds bytes (two pixels per byte, high nibble first)."""
    h, w = indices.shape
    if w % 2:
        raise ValueError('image width must be even, got %d' % w)
    packed = (indices[:, 0::2] << 4) | indices[:, 1::2]
    return packed.astype(np.uint8).tobytes()


def glds_to_indices(data, width=WIDTH, height=HEIGHT):
    """.glds bytes -> (h, w) uint8 palette indices. Used to preview existing files."""
    packed = np.frombuffer(data, dtype=np.uint8).reshape(height, width // 2)
    out = np.empty((height, width), dtype=np.uint8)
    out[:, 0::2] = packed >> 4
    out[:, 1::2] = packed & 0x0F
    return out
