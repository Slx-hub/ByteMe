"""PicToBytes - crop, grade, dither and ship images to the 7-colour e-paper frame.

Run with:  python app.py
Then open: http://localhost:5000
"""

import io
import os
import traceback

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory
from PIL import Image
from werkzeug.exceptions import HTTPException

from pictobytes.config import BASE_DIR, config
from pictobytes.dither import colour_histogram
from pictobytes.library import library, png_bytes
from pictobytes.palette import (PALETTE_HEX, PALETTE_NAMES, WIDTH, HEIGHT,
                                indices_to_glds, indices_to_rgb)
from pictobytes.pipeline import (DEFAULT_SETTINGS, load_source, render_indices,
                                 render_preview_source)

app = Flask(__name__, static_folder=None)

# The crop canvas never needs the full 5120px original.
CANVAS_MAX = 1600


def png_response(image, max_age=0):
    response = send_file(io.BytesIO(png_bytes(image)), mimetype='image/png')
    response.headers['Cache-Control'] = 'public, max-age=%d' % max_age if max_age else 'no-store'
    return response


@app.errorhandler(Exception)
def handle_error(error):
    # Routing and other HTTP errors already carry the right status.
    if isinstance(error, HTTPException):
        return jsonify({'error': error.description}), error.code
    if isinstance(error, KeyError):
        return jsonify({'error': 'unknown image %s' % error}), 404
    if isinstance(error, FileNotFoundError):
        return jsonify({'error': str(error)}), 404
    traceback.print_exc()
    return jsonify({'error': str(error)}), 500


# ----------------------------------------------------------------- static

def _asset_stamp(name):
    try:
        return str(int(os.path.getmtime(os.path.join(BASE_DIR, 'static', name))))
    except OSError:
        return '0'


@app.route('/')
def index():
    """Serve the page with mtime-stamped asset URLs.

    Without this the browser happily keeps running a cached app.js after an
    edit, which looks exactly like the change not working.
    """
    with open(os.path.join(BASE_DIR, 'static', 'index.html'), encoding='utf-8') as handle:
        page = handle.read()
    for name in ('app.js', 'style.css'):
        page = page.replace('/static/' + name, '/static/%s?v=%s' % (name, _asset_stamp(name)))
    response = app.make_response(page)
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/favicon.ico')
def favicon():
    # A flat tile in the panel's own green, just to stop the 404.
    pixel = Image.new('RGB', (32, 32), (0x52, 0x77, 0x43))
    return png_response(pixel, max_age=86400)


@app.route('/static/<path:filename>')
def static_files(filename):
    response = send_from_directory(os.path.join(BASE_DIR, 'static'), filename)
    response.headers['Cache-Control'] = 'no-cache'
    return response


# ---------------------------------------------------------------- library

@app.route('/api/library')
def api_library():
    return jsonify({
        'images': library.all_status(),
        'palette': [{'hex': '#' + h, 'name': n} for h, n in zip(PALETTE_HEX, PALETTE_NAMES)],
        'defaults': DEFAULT_SETTINGS,
        'config': {
            'device_url': config['device_url'],
            'deploy_dir': config['deploy_dir'],
            'source_dir': config['source_dir'],
            'default_green_reduce': config['default_green_reduce'],
        },
        'frame': {'width': WIDTH, 'height': HEIGHT},
    })


@app.route('/api/scan', methods=['POST'])
def api_scan():
    added = library.scan()
    return jsonify({'added': added, 'images': library.all_status()})


@app.route('/api/config', methods=['POST'])
def api_config():
    for key, value in (request.get_json(silent=True) or {}).items():
        if key in config:
            config[key] = value
    config.save()
    return jsonify({'ok': True})


# ------------------------------------------------------------------ image

@app.route('/api/image/<key>/source')
def api_source(key):
    """Downscaled original for the crop canvas.

    Framing is stored normalised, so the canvas copy can be as small as we like;
    the real render always goes back to the full-resolution file. JPEG keeps a
    40 megapixel phone photo down to a couple of hundred kilobytes.
    """
    settings = library.get(key)['settings']
    image = load_source(library.source_path(key), settings)
    if max(image.size) > CANVAS_MAX:
        image.thumbnail((CANVAS_MAX, CANVAS_MAX), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=88)
    response = send_file(io.BytesIO(buffer.getvalue()), mimetype='image/jpeg')
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/image/<key>/render')
def api_render_png(key):
    path = library.render_path(key)
    if not os.path.exists(path):
        raise FileNotFoundError('%s has not been rendered yet' % key)
    return send_file(path, mimetype='image/png', max_age=0)


@app.route('/api/image/<key>/settings', methods=['POST'])
def api_settings(key):
    settings = library.update_settings(key, request.get_json(silent=True) or {})
    return jsonify({'settings': settings, 'status': library.status(key)})


@app.route('/api/image/<key>/preview', methods=['POST'])
def api_preview(key):
    """Dither with the posted settings without saving anything. Drives the live view."""
    body = request.get_json(silent=True) or {}
    settings = {**library.get(key)['settings'], **(body.get('settings') or {})}

    if body.get('stage') == 'source':
        return png_response(render_preview_source(library.source_path(key), settings))

    indices, _ = render_indices(library.source_path(key), settings)
    response = png_response(Image.fromarray(indices_to_rgb(indices)))
    response.headers['X-Histogram'] = ','.join('%.5f' % v for v in colour_histogram(indices))
    return response


@app.route('/api/image/<key>/render', methods=['POST'])
def api_render(key):
    return jsonify(library.render(key))


@app.route('/api/image/<key>/deploy', methods=['POST'])
def api_deploy(key):
    return jsonify(library.deploy(key))


@app.route('/api/image/<key>/undeploy', methods=['POST'])
def api_undeploy(key):
    return jsonify(library.undeploy(key))


@app.route('/api/image/<key>', methods=['DELETE'])
def api_delete(key):
    library.delete(key)
    return jsonify({'ok': True})


# ------------------------------------------------------------------ batch

@app.route('/api/render_all', methods=['POST'])
def api_render_all():
    only_stale = (request.get_json(silent=True) or {}).get('only_stale', True)
    rendered, failed = [], []
    for status in library.all_status():
        if only_stale and not status['stale']:
            continue
        try:
            library.render(status['name'])
            rendered.append(status['name'])
        except Exception as error:
            failed.append({'name': status['name'], 'error': str(error)})
    return jsonify({'rendered': rendered, 'failed': failed, 'images': library.all_status()})


@app.route('/api/deploy_all', methods=['POST'])
def api_deploy_all():
    deployed, failed = [], []
    for status in library.all_status():
        if not status['has_glds']:
            continue
        try:
            library.deploy(status['name'])
            deployed.append(status['name'])
        except Exception as error:
            failed.append({'name': status['name'], 'error': str(error)})
    return jsonify({'deployed': deployed, 'failed': failed, 'images': library.all_status()})


# ----------------------------------------------------------------- device

@app.route('/api/image/<key>/send', methods=['POST'])
def api_send(key):
    """Push one image straight to the ESP32, bypassing GLaDOS. For colour grading."""
    body = request.get_json(silent=True) or {}
    if body.get('live'):
        # Send exactly what the editor is showing, without rendering to disk first.
        settings = {**library.get(key)['settings'], **(body.get('settings') or {})}
        indices, _ = render_indices(library.source_path(key), settings)
        payload = indices_to_glds(indices)
    else:
        payload = library.glds_bytes(key)

    response = requests.post(
        config['device_url'].rstrip('/') + '/image',
        headers={'Content-Type': 'application/octet-stream'},
        data=payload,
        timeout=config['device_timeout'],
    )
    return jsonify({'status': response.status_code, 'bytes': len(payload), 'body': response.text[:200]})


@app.route('/api/device/clear', methods=['POST'])
def api_clear():
    colour = (request.get_json(silent=True) or {}).get('color', 1)
    response = requests.get(
        '%s/clear?color=%d' % (config['device_url'].rstrip('/'), int(colour)),
        timeout=config['device_timeout'],
    )
    return jsonify({'status': response.status_code, 'body': response.text[:200]})


if __name__ == '__main__':
    print('PicToBytes')
    print('  library : %d images' % len(library.entries))
    print('  deploy  : %s' % config.deploy_dir)
    print('  device  : %s' % config['device_url'])
    print('  open    : http://localhost:%d' % config['port'])
    app.run(host='0.0.0.0', port=config['port'], debug=False, threaded=True)
