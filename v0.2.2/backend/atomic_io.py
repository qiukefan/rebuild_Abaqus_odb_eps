"""Small, dependency-free JSON publisher shared with Abaqus Python.

Windows readers can briefly deny replacement of an open file. Keep the old
complete JSON until replacement succeeds; never truncate or delete the target.
"""
import errno
import json
import os
import sys
import time
import uuid


def is_windows_lock(error):
    code = getattr(error, 'winerror', None)
    return code in (5, 32, 33) or (
        code is None and os.name == 'nt' and
        isinstance(error, PermissionError) and error.errno == errno.EACCES)


def atomic_json(path, value, *, timeout=2.0):
    path = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temporary = os.path.join(directory, '.' + os.path.basename(path) + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':'))
        deadline = time.monotonic() + max(0.0, timeout)
        delay = .02
        while True:
            try:
                os.replace(temporary, path)
                return
            except OSError as error:
                remaining = deadline - time.monotonic()
                if not is_windows_lock(error) or remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(.1, delay * 2)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        except OSError:
            # Do not mask the original write error if a scanner also holds the
            # disposable temporary file. Never remove the previous target.
            pass


def write_progress(path, value, *, timeout=2.0):
    """A locked progress file may miss an update; data writes stay strict."""
    try:
        atomic_json(path, value, timeout=timeout)
        return True
    except OSError as error:
        if not is_windows_lock(error):
            raise
        if sys.stderr is not None:
            try:
                print('Progress update skipped while file is in use: {}'.format(error),
                      file=sys.stderr, flush=True)
            except OSError:
                pass
        return False
