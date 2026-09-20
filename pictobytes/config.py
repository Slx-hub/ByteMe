"""User-editable settings, persisted next to the app as config.json."""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

DEFAULTS = {
    # Where new source photos are picked up from.
    'source_dir': 'original',
    # Rendered 800x480 dithered PNGs (kept for eyeballing and byte_me.py compatibility).
    'render_dir': 'input',
    # Generated .glds payloads.
    'output_dir': 'output',
    # GLaDOS picks a random .glds from here for the scheduled rotation.
    'deploy_dir': 'C:/dev/Glados/GLaDOSHomeAssistant/lib/pic_frame_images',
    # The ESP32 itself, for pushing a single image while colour grading.
    'device_url': 'http://192.168.178.42',
    'device_timeout': 30,
    # Applied to every new image until you override it per image.
    'default_green_reduce': 0.0,
    'port': 5000,
}


def _resolve(path):
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


class Config(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as handle:
                self.update(json.load(handle))
        else:
            self.save()

    def save(self):
        with open(CONFIG_PATH, 'w', encoding='utf-8') as handle:
            json.dump(dict(self), handle, indent=2)

    @property
    def source_dir(self):
        return _resolve(self['source_dir'])

    @property
    def render_dir(self):
        return _resolve(self['render_dir'])

    @property
    def output_dir(self):
        return _resolve(self['output_dir'])

    @property
    def deploy_dir(self):
        return _resolve(self['deploy_dir'])


config = Config()
