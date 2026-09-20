"""Talking to the picture frame itself.

The panel takes ~30s to refresh and then rests for 2 minutes, and the firmware
refuses every refresh path until that has elapsed. Posting into that window
gets a 503 whose body is easy to mistake for a successful send, so every
refresh here checks `/status` first and reports what the panel actually said.

Firmware endpoints (Arduino-Collection/smart_picture_frame/wifi):
    GET  /status          panel state plus diagnostics
    GET  /clear?color=N   clear to one palette colour
    POST /image           one full 192000 byte frame
"""

import requests

from .config import config
from .palette import HEIGHT, WIDTH

PAYLOAD_BYTES = WIDTH * HEIGHT // 2

# What /status can report. Only 'ready' will accept a refresh.
READY = 'ready'
STATE_MESSAGES = {
    'busy': 'The panel is mid-refresh. That takes about 30 seconds.',
    'resting': 'The panel is resting after its last refresh.',
    'uninitialized': 'The panel has not finished starting up.',
    'error': 'The panel reports an error. Check it, or power cycle it.',
}


class DeviceError(Exception):
    """Something stopped the frame accepting an image. `http_status` is what to
    return to the browser; `message` is written to be read by a person."""

    def __init__(self, message, http_status=502, state=None, rest_left_s=None):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.state = state
        self.rest_left_s = rest_left_s


def _base():
    return config['device_url'].rstrip('/')


def _timeout():
    return config['device_timeout']


def status():
    """Current panel state. Raises DeviceError if the frame cannot be reached."""
    url = _base() + '/status'
    try:
        response = requests.get(url, timeout=min(_timeout(), 8))
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        raise DeviceError('The frame at %s did not answer in time. Is it awake and on '
                          'the network?' % _base(), http_status=504)
    except requests.RequestException as error:
        raise DeviceError('Cannot reach the frame at %s (%s).' % (_base(), error),
                          http_status=502)
    except ValueError:
        raise DeviceError('The frame answered /status with something that is not JSON.',
                          http_status=502)


def _describe_wait(state, rest_left_s):
    message = STATE_MESSAGES.get(state, 'The panel reports %r.' % state)
    if rest_left_s:
        message += ' Ready again in %s.' % _format_seconds(rest_left_s)
    else:
        message += ' Try again shortly.'
    return message


def _format_seconds(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return '%ds' % seconds
    return '%dm %02ds' % divmod(seconds, 60)


def ensure_ready():
    """Return the status dict, or raise DeviceError explaining the wait."""
    state = status()
    if state.get('status') != READY:
        raise DeviceError(
            _describe_wait(state.get('status'), state.get('rest_left_s')),
            http_status=409,
            state=state.get('status'),
            rest_left_s=state.get('rest_left_s'),
        )
    return state


def send_image(payload):
    """Check the panel is ready, then upload one frame."""
    if len(payload) != PAYLOAD_BYTES:
        raise DeviceError('Refusing to send %d bytes; the panel needs exactly %d.'
                          % (len(payload), PAYLOAD_BYTES), http_status=400)

    ensure_ready()

    try:
        response = requests.post(
            _base() + '/image',
            headers={'Content-Type': 'application/octet-stream'},
            data=payload,
            timeout=_timeout(),
        )
    except requests.Timeout:
        raise DeviceError('The frame stopped responding part way through the upload. '
                          'Check /status before resending.', http_status=504)
    except requests.RequestException as error:
        raise DeviceError('Upload to the frame failed (%s).' % error, http_status=502)

    return _interpret(response, len(payload))


def clear(colour=1):
    """Clear the panel to one palette colour. Same readiness rules as an image."""
    ensure_ready()
    try:
        response = requests.get('%s/clear?color=%d' % (_base(), int(colour)),
                                timeout=_timeout())
    except requests.RequestException as error:
        raise DeviceError('Clear failed (%s).' % error, http_status=502)
    return _interpret(response, 0)


def _interpret(response, sent_bytes):
    """Turn the firmware's reply into either a result or a readable error."""
    body = (response.text or '').strip()

    if response.status_code == 503:
        # Lost a race against the rest period, or a button refresh got in first.
        raise DeviceError('The panel became busy before the upload landed. %s'
                          % (body or 'Try again in a couple of minutes.'),
                          http_status=409)
    if response.status_code == 422:
        # The firmware leaves the panel untouched in this case.
        raise DeviceError('Only part of the image arrived, so the frame rejected it '
                          '(%s). The picture on the panel is unchanged.' % (body or '422'),
                          http_status=502)
    if not response.ok:
        raise DeviceError('The frame answered %d: %s' % (response.status_code, body or '-'),
                          http_status=502)

    return {'status': response.status_code, 'bytes': sent_bytes, 'body': body[:200]}
