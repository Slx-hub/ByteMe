"""The image library: metadata, folder scanning, rendering and deployment."""

import io
import json
import os
import shutil
import threading
import time

from PIL import Image

from .config import BASE_DIR, config
from .dither import colour_histogram
from .palette import indices_to_glds, indices_to_rgb
from .pipeline import DEFAULT_SETTINGS, clamp_center, merge_settings, render_indices

LIBRARY_PATH = os.path.join(BASE_DIR, 'library.json')
SOURCE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tif', '.tiff'}

_lock = threading.RLock()


def _listdir(path):
    if not os.path.isdir(path):
        return []
    return sorted(os.listdir(path))


def _is_image(name):
    return os.path.splitext(name)[1].lower() in SOURCE_EXTENSIONS


def _posix(path):
    return path.replace(os.sep, '/')


class Library:
    def __init__(self):
        self.entries = {}
        self.load()

    # ------------------------------------------------------------------ store

    def load(self):
        if os.path.exists(LIBRARY_PATH):
            with open(LIBRARY_PATH, 'r', encoding='utf-8') as handle:
                self.entries = json.load(handle).get('images', {})
        self.scan()

    def save(self):
        with _lock:
            tmp = LIBRARY_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump({'version': 1, 'images': self.entries}, handle, indent=2, sort_keys=True)
            os.replace(tmp, LIBRARY_PATH)

    # ------------------------------------------------------------------- scan

    def scan(self):
        """Pick up new files. Returns the names that were added."""
        with _lock:
            added = []

            # Real source photos win: they can be reframed and regraded.
            for name in _listdir(config.source_dir):
                if not _is_image(name):
                    continue
                key = os.path.splitext(name)[0]
                relative = _posix(os.path.join(os.path.basename(config['source_dir']), name))
                entry = self.entries.get(key)
                if entry is None:
                    self.entries[key] = self._new_entry(key, relative, legacy=False)
                    added.append(key)
                elif entry.get('legacy'):
                    # An original turned up for what used to be a render-only entry.
                    entry['source'] = relative
                    entry['legacy'] = False
                    entry['settings'] = dict(DEFAULT_SETTINGS)
                    entry['stale'] = True
                    added.append(key)

            # Images that only exist as an already-dithered render from the old
            # workflow. Still listed, convertible and deployable, just not editable.
            for name in _listdir(config.render_dir):
                if not _is_image(name):
                    continue
                key = os.path.splitext(name)[0]
                if key in self.entries:
                    continue
                relative = _posix(os.path.join(os.path.basename(config['render_dir']), name))
                self.entries[key] = self._new_entry(key, relative, legacy=True)
                added.append(key)

            for key in list(self.entries):
                if not os.path.exists(self.source_path(key)):
                    del self.entries[key]

            self.save()
            return added

    def _new_entry(self, key, relative_source, legacy):
        settings = dict(DEFAULT_SETTINGS)
        if not legacy:
            settings['green_reduce'] = float(config['default_green_reduce'])
        return {
            'name': key,
            'source': relative_source,
            'legacy': legacy,
            'settings': settings,
            'rendered_at': None,
            'source_mtime': None,
            'histogram': None,
            'stale': True,
        }

    # ------------------------------------------------------------------ paths

    def source_path(self, key):
        return os.path.join(BASE_DIR, self.entries[key]['source'])

    def render_path(self, key):
        return os.path.join(config.render_dir, key + '.png')

    def glds_path(self, key):
        return os.path.join(config.output_dir, key + '.glds')

    def deploy_path(self, key):
        return os.path.join(config.deploy_dir, key + '.glds')

    # ------------------------------------------------------------------- read

    def get(self, key):
        entry = self.entries.get(key)
        if entry is None:
            raise KeyError(key)
        return entry

    def status(self, key):
        """Fresh view of an entry plus everything the UI needs to show state."""
        entry = dict(self.get(key))
        source = self.source_path(key)
        mtime = os.path.getmtime(source) if os.path.exists(source) else None

        rendered = os.path.exists(self.render_path(key))
        has_glds = os.path.exists(self.glds_path(key))
        deployed = os.path.exists(self.deploy_path(key))

        stale = bool(entry.get('stale')) or not rendered or not has_glds
        if mtime and entry.get('source_mtime') and mtime > entry['source_mtime'] + 0.5:
            stale = True

        try:
            with Image.open(source) as image:
                entry['source_size'] = list(image.size)
        except Exception:
            entry['source_size'] = None

        entry['rendered'] = rendered
        entry['has_glds'] = has_glds
        entry['deployed'] = deployed
        entry['deploy_outdated'] = bool(
            deployed and has_glds
            and os.path.getmtime(self.glds_path(key)) > os.path.getmtime(self.deploy_path(key)) + 0.5
        )
        entry['stale'] = stale
        return entry

    def all_status(self):
        return [self.status(key) for key in sorted(self.entries)]

    # ------------------------------------------------------------------ write

    def update_settings(self, key, settings):
        with _lock:
            entry = self.get(key)
            merged = merge_settings({**entry['settings'], **(settings or {})})
            # Snap the framing to what the renderer will actually use.
            try:
                with Image.open(self.source_path(key)) as image:
                    merged['cx'], merged['cy'] = clamp_center(image.size, merged)
            except Exception:
                pass
            if merged != entry['settings']:
                entry['settings'] = merged
                entry['stale'] = True
                self.save()
            return merged

    def render(self, key):
        """Render to input/<key>.png and output/<key>.glds. Returns the status."""
        with _lock:
            settings = dict(self.get(key)['settings'])

        source = self.source_path(key)
        indices, _ = render_indices(source, settings)

        os.makedirs(config.render_dir, exist_ok=True)
        os.makedirs(config.output_dir, exist_ok=True)
        # For a legacy entry the render *is* the source, so leave it alone.
        if not self.get(key).get('legacy'):
            Image.fromarray(indices_to_rgb(indices)).save(self.render_path(key))
        with open(self.glds_path(key), 'wb') as handle:
            handle.write(indices_to_glds(indices))

        with _lock:
            entry = self.get(key)
            entry['rendered_at'] = time.time()
            entry['source_mtime'] = os.path.getmtime(source)
            entry['histogram'] = colour_histogram(indices)
            entry['stale'] = False
            self.save()
        return self.status(key)

    def deploy(self, key):
        """Copy the .glds into the folder GLaDOS draws its random images from."""
        glds = self.glds_path(key)
        if not os.path.exists(glds):
            raise FileNotFoundError('%s has not been rendered yet' % key)
        os.makedirs(config.deploy_dir, exist_ok=True)
        shutil.copy2(glds, self.deploy_path(key))
        return self.status(key)

    def undeploy(self, key):
        target = self.deploy_path(key)
        if os.path.exists(target):
            os.remove(target)
        return self.status(key)

    def glds_bytes(self, key):
        with open(self.glds_path(key), 'rb') as handle:
            return handle.read()

    def delete(self, key):
        """Remove the entry and everything generated from it, including the source."""
        with _lock:
            for path in (self.render_path(key), self.glds_path(key), self.deploy_path(key)):
                if os.path.exists(path):
                    os.remove(path)
            source = self.source_path(key)
            if os.path.exists(source):
                os.remove(source)
            self.entries.pop(key, None)
            self.save()


def png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


library = Library()
