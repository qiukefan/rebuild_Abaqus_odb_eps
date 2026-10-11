"""Windows status publication races must not turn successful ODB reads into errors."""
from contextlib import contextmanager, redirect_stderr
import errno
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
import atomic_io
import extract_odb


def sharing_error(code=32):
    error = PermissionError(errno.EACCES, 'Synthetic Windows sharing conflict')
    error.winerror = code
    return error


@contextmanager
def deny_delete_share(path):
    """A real readable Win32 handle which forbids replacement until it closes."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # GENERIC_READ, FILE_SHARE_READ|FILE_SHARE_WRITE (no DELETE), OPEN_EXISTING.
    handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


class AtomicJsonTests(unittest.TestCase):
    def test_windows_error_classification_does_not_hide_unrelated_errors(self):
        for code in (5, 32, 33):
            self.assertTrue(atomic_io.is_windows_lock(sharing_error(code)))
        self.assertFalse(atomic_io.is_windows_lock(sharing_error(112)))
        self.assertFalse(atomic_io.is_windows_lock(OSError(errno.ENOSPC, 'Disk full')))
        self.assertFalse(atomic_io.is_windows_lock(FileNotFoundError('Missing parent')))

    def test_transient_lock_retries_without_changing_old_json_until_success(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'status.json'
            old = b'{"stage":"old"}'
            target.write_bytes(old)
            real_replace = os.replace
            attempts = []

            def replace_after_two_conflicts(source, destination):
                attempts.append((source, destination))
                self.assertEqual(target.read_bytes(), old)
                if len(attempts) < 3:
                    raise sharing_error(5 if len(attempts) == 1 else 32)
                return real_replace(source, destination)

            with patch.object(atomic_io.os, 'replace', side_effect=replace_after_two_conflicts), \
                 patch.object(atomic_io.time, 'sleep') as sleep:
                atomic_io.atomic_json(target, {'stage': 'new', 'message': '读取完成'})
            self.assertEqual(len(attempts), 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['message'], '读取完成')
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_persistent_lock_times_out_preserves_old_file_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'status.json'
            target.write_bytes(b'{"old":true}')
            error = sharing_error()
            started = time.monotonic()
            with patch.object(atomic_io.os, 'replace', side_effect=error) as replace:
                with self.assertRaises(PermissionError) as raised:
                    atomic_io.atomic_json(target, {'new': True}, timeout=.03)
            self.assertIs(raised.exception, error)
            self.assertGreaterEqual(replace.call_count, 2)
            self.assertLess(time.monotonic()-started, 2)
            self.assertEqual(target.read_bytes(), b'{"old":true}')
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_non_lock_error_propagates_immediately_with_no_partial_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'status.json'
            error = OSError(errno.ENOSPC, 'Disk full')
            with patch.object(atomic_io.os, 'replace', side_effect=error) as replace, \
                 patch.object(atomic_io.time, 'sleep') as sleep:
                with self.assertRaises(OSError) as raised:
                    atomic_io.atomic_json(target, {'new': True})
            self.assertIs(raised.exception, error)
            replace.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_invalid_json_never_changes_old_target_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'status.json'
            target.write_bytes(b'{"old":true}')
            with self.assertRaises(ValueError):
                atomic_io.atomic_json(target, {'invalid': float('nan')})
            self.assertEqual(target.read_bytes(), b'{"old":true}')
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_progress_only_downgrades_windows_lock_and_logs_it(self):
        log = io.StringIO()
        with patch.object(atomic_io, 'atomic_json', side_effect=sharing_error(33)), redirect_stderr(log):
            self.assertFalse(atomic_io.write_progress('status.json', {'stage': 'reading'}, timeout=0))
        self.assertIn('Progress update skipped', log.getvalue())
        error = OSError(errno.ENOSPC, 'Disk full')
        with patch.object(atomic_io, 'atomic_json', side_effect=error):
            with self.assertRaises(OSError) as raised:
                atomic_io.write_progress('status.json', {}, timeout=0)
        self.assertIs(raised.exception, error)

    def test_progress_returns_true_after_successful_atomic_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'status.json'
            self.assertTrue(atomic_io.write_progress(target, {'stage': 'done'}))
            self.assertEqual(json.loads(target.read_text()), {'stage': 'done'})

    @unittest.skipUnless(os.name == 'nt', 'Requires actual Windows file sharing semantics')
    def test_real_win32_lock_reproduces_old_failure_and_retry_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'status.json'
            target.write_bytes(b'{"stage":"old"}')
            old_temporary = Path(directory)/'old-writer.tmp'
            old_temporary.write_bytes(b'{"stage":"new"}')
            started, failures = threading.Event(), []

            def writer():
                started.set()
                try:
                    atomic_io.atomic_json(target, {'stage': 'recovered'}, timeout=2)
                except BaseException as error:
                    failures.append(error)

            with deny_delete_share(target):
                with self.assertRaises(OSError) as raised:
                    os.replace(old_temporary, target)  # Exact former one-shot operation.
                self.assertTrue(atomic_io.is_windows_lock(raised.exception))
                old_temporary.unlink()
                thread = threading.Thread(target=writer)
                thread.start()
                self.assertTrue(started.wait(1))
                time.sleep(.12)
                self.assertEqual(target.read_bytes(), b'{"stage":"old"}')
            thread.join(3)
            self.assertFalse(thread.is_alive(), 'Writer failed to stop after lock release')
            self.assertEqual(failures, [])
            self.assertEqual(json.loads(target.read_text()), {'stage': 'recovered'})
            self.assertEqual(list(Path(directory).iterdir()), [target])

    @unittest.skipUnless(os.name == 'nt', 'Requires actual Windows file sharing semantics')
    def test_real_win32_persistent_lock_keeps_old_json_and_skips_only_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'status.json'
            target.write_bytes(b'{"stage":"old"}')
            with deny_delete_share(target):
                with self.assertRaises(OSError) as raised:
                    atomic_io.atomic_json(target, {'stage': 'strict'}, timeout=.04)
                self.assertTrue(atomic_io.is_windows_lock(raised.exception))
                with redirect_stderr(io.StringIO()):
                    self.assertFalse(atomic_io.write_progress(target, {'stage': 'progress'}, timeout=.01))
                self.assertEqual(target.read_bytes(), b'{"stage":"old"}')
                self.assertEqual(list(Path(directory).iterdir()), [target])


class ExtractorPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.source = self.folder/'original.odb'
        self.source.write_bytes(b'read-only source fixture')
        self.status = self.folder/'status.json'
        self.status.write_bytes(b'{"stage":"previous"}')
        self.output = self.folder/'extraction.json'
        self.request = self.folder/'request.json'
        self.request.write_text(json.dumps(dict(odb_path=str(self.source), output_path=str(self.output),
                                                status_path=str(self.status))), encoding='utf-8')

    def run_main(self):
        with patch.object(sys, 'argv', ['extract_odb.py', '--request', str(self.request)]):
            extract_odb.main()

    def test_locked_progress_during_and_after_extraction_does_not_discard_output(self):
        result = {'instances': [], 'metadata': {'read_only': True}}
        real_replace = os.replace
        conflicts = []

        def selective_lock(source, destination):
            if Path(destination) == self.status:
                conflicts.append(destination)
                raise sharing_error(5)
            return real_replace(source, destination)

        def extract_stub(request, status):
            status(stage='odb.open', message='Reading')
            status(stage='odb.done', message='Source closed')
            return result

        with patch.object(atomic_io.os, 'replace', side_effect=selective_lock), \
             patch.object(extract_odb, 'extract', side_effect=extract_stub), \
             patch.object(extract_odb, 'write_progress', side_effect=lambda p, v: atomic_io.write_progress(p, v, timeout=0)), \
             redirect_stderr(io.StringIO()):
            self.run_main()
        self.assertEqual(json.loads(self.output.read_text()), result)
        self.assertEqual(len(conflicts), 3)  # open, done, and final odb.complete.
        self.assertEqual(self.status.read_bytes(), b'{"stage":"previous"}')
        self.assertEqual(self.source.read_bytes(), b'read-only source fixture')
        self.assertEqual(list(self.folder.glob('*.tmp')), [])

    def test_real_data_publication_failure_is_not_swallowed_as_progress(self):
        error = OSError(errno.ENOSPC, 'Output disk full')
        with patch.object(extract_odb, 'extract', return_value={'instances': []}), \
             patch.object(extract_odb, 'atomic_json', side_effect=error), \
             patch.object(extract_odb, 'write_progress', return_value=False):
            with self.assertRaises(OSError) as raised:
                self.run_main()
        self.assertIs(raised.exception, error)
        self.assertFalse(self.output.exists())

        self.assertFalse((self.folder/'extraction-error.json').exists())

    def test_failed_error_notification_preserves_original_extraction_exception(self):
        error = ValueError('ODB does not contain the EPS-selected frame')
        with patch.object(extract_odb, 'extract', side_effect=error), \
             patch.object(extract_odb, 'write_progress', side_effect=OSError(errno.ENOSPC, 'Status disk full')):
            with self.assertRaises(ValueError) as raised:
                self.run_main()
        self.assertIs(raised.exception, error)
        self.assertFalse(self.output.exists())
        failure = json.loads((self.folder/'extraction-error.json').read_text(encoding='utf-8'))
        self.assertEqual(failure['message'], str(error))


if __name__ == '__main__':
    unittest.main()
