"""Copying deployed images straight onto the Pi that runs GLaDOS.

The local deploy folder is GLaDOS' git checkout on this PC, so on its own a
deploy only reaches the Pi after a commit, a push and a pull there. This skips
that round trip by scp'ing the .glds straight into the folder on the Pi.

Uses the system ssh and scp with BatchMode, so it needs key auth to the host
and never sits waiting on a password prompt.
"""

import shlex
import subprocess

from .config import config


class RemoteError(Exception):
    pass


def enabled():
    return bool(config['remote_host'])


def _host():
    return config['remote_host']


def _dir():
    return config['remote_dir'].rstrip('/')


def _options():
    # ConnectTimeout bounds an unreachable Pi; the subprocess timeout bounds the rest.
    return ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=%d' % min(config['remote_timeout'], 8)]


def _run(args, cwd=None):
    try:
        result = subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=config['remote_timeout'])
    except FileNotFoundError:
        raise RemoteError('%s is not installed or not on PATH.' % args[0])
    except subprocess.TimeoutExpired:
        raise RemoteError('%s did not answer within %ds.' % (_host(), config['remote_timeout']))
    if result.returncode != 0:
        # scp's own last line is just "Connection closed"; the reason comes before it.
        lines = [line.strip() for line in result.stderr.decode('utf-8', 'replace').splitlines()]
        message = '; '.join(line for line in lines if line)
        raise RemoteError(message or '%s exited with %d' % (args[0], result.returncode))


def push(folder, names):
    """Copy each folder/<name> into remote_dir."""
    if not names:
        return
    # Run from the folder and pass bare names: scp reads a drive letter as a host
    # name, and the './' stops a name that starts with '-' reading as a flag.
    # No -q: it would also swallow ssh's reason for a failed connection.
    _run(['scp'] + _options() + ['./' + name for name in names]
         + ['%s:%s/' % (_host(), _dir())], cwd=folder)


def remove(names):
    """Delete each name from remote_dir."""
    if not names:
        return
    _run(['ssh'] + _options() + [_host(), 'cd %s && rm -f -- %s' % (
        shlex.quote(_dir()), ' '.join(shlex.quote(name) for name in names))])


def attempt(action, *args):
    """Run a remote step without letting it fail the local one it follows.

    Returns what the UI reports: None when no Pi is configured, otherwise whether
    it worked and, if not, why.
    """
    if not enabled():
        return None
    try:
        action(*args)
    except RemoteError as error:
        return {'ok': False, 'host': _host(), 'error': str(error)}
    return {'ok': True, 'host': _host()}
