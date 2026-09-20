"""Source image -> framed 800x480 -> tonal adjustments -> dither -> indices."""

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from .dither import dither, quantize_no_dither
from .palette import WIDTH, HEIGHT

TARGET_ASPECT = WIDTH / HEIGHT

DEFAULT_SETTINGS = {
    # Framing: cx/cy are the normalised source point sitting at the centre of the
    # frame; zoom 1.0 is the tightest fit that still fills the frame completely.
    'cx': 0.5,
    'cy': 0.5,
    'zoom': 1.0,
    'rotate': 0,
    # Tonal
    'brightness': 1.0,
    'contrast': 1.0,
    'saturation': 1.0,
    'sharpen': 0.0,
    # Panel-specific
    'green_reduce': 0.0,
    'dither_enabled': True,
}


def merge_settings(settings):
    merged = dict(DEFAULT_SETTINGS)
    if settings:
        for key, value in settings.items():
            if key in merged:
                merged[key] = value
    return merged


def cover_scale(source_size):
    """Scale at which the source exactly fills the 800x480 frame (zoom == 1.0)."""
    width, height = source_size
    return max(WIDTH / width, HEIGHT / height)


def crop_box(source_size, settings):
    """The rectangle in source pixel coordinates that becomes the frame."""
    width, height = source_size
    zoom = max(1.0, float(settings['zoom']))
    scale = cover_scale(source_size) * zoom

    crop_w = WIDTH / scale
    crop_h = HEIGHT / scale

    # Keep the frame inside the image: clamp the centre to the legal range.
    half_w = crop_w / 2.0
    half_h = crop_h / 2.0
    cx = min(max(float(settings['cx']) * width, half_w), width - half_w)
    cy = min(max(float(settings['cy']) * height, half_h), height - half_h)

    return (cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def clamp_center(source_size, settings):
    """Normalised centre after clamping, so the UI can snap its handles."""
    width, height = source_size
    left, top, right, bottom = crop_box(source_size, settings)
    return ((left + right) / 2.0 / width, (top + bottom) / 2.0 / height)


def load_source(path, settings):
    image = Image.open(path)
    image = image.convert('RGB')
    rotate = int(settings.get('rotate', 0)) % 360
    if rotate:
        image = image.rotate(-rotate, expand=True, resample=Image.BICUBIC)
    return image


def frame(image, settings):
    """Crop and scale to exactly 800x480."""
    box = crop_box(image.size, settings)
    return image.resize((WIDTH, HEIGHT), Image.LANCZOS, box=box)


def adjust(image, settings):
    """Tonal work, applied after scaling so sharpening matches the final pixels."""
    brightness = float(settings['brightness'])
    contrast = float(settings['contrast'])
    saturation = float(settings['saturation'])
    sharpen = float(settings['sharpen'])

    if brightness != 1.0:
        image = ImageEnhance.Brightness(image).enhance(brightness)
    if contrast != 1.0:
        image = ImageEnhance.Contrast(image).enhance(contrast)
    if saturation != 1.0:
        image = ImageEnhance.Color(image).enhance(saturation)
    if sharpen > 0.0:
        image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=int(sharpen * 150), threshold=2))
    return image


def render_preview_source(path, settings):
    """The 800x480 full-colour image that goes into the dither. Shown as 'before'."""
    settings = merge_settings(settings)
    return adjust(frame(load_source(path, settings), settings), settings)


def render_indices(path, settings):
    """Full pipeline: returns (palette indices, the 800x480 full-colour source)."""
    settings = merge_settings(settings)
    staged = render_preview_source(path, settings)
    array = np.asarray(staged)
    if settings['dither_enabled']:
        indices = dither(array, float(settings['green_reduce']))
    else:
        indices = quantize_no_dither(array, float(settings['green_reduce']))
    return indices, staged
