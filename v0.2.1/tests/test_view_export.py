"""Run the Abaqus helper against a fake host; no Abaqus session is modified."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'ExportCurrentView_v0.2.1.py'


def viewport(mode='NONUNIFORM', value=(1, 1, 10)):
    field = {'AUTO': 'autoDeformationScaleValue', 'UNIFORM': 'uniformScaleFactor',
             'NONUNIFORM': 'nonuniformScaleFactor'}.get(mode, 'unused')
    # Other modes' attributes are absent on purpose: accessing them is a bug.
    options = SimpleNamespace(deformationScaling=mode, **{field: value})
    view = SimpleNamespace(cameraPosition=(1., 2., 3.), cameraTarget=(0., 0., 0.),
                           cameraUpVector=(0., 1., 0.), width=100., height=80.,
                           viewOffsetX=0., viewOffsetY=0., nearPlane=1.,
                           farPlane=1000., projection='PERSPECTIVE')
    return SimpleNamespace(view=view, displayedObject=SimpleNamespace(steps={}, rootAssembly=object()),
                           odbDisplay=SimpleNamespace(commonOptions=options))


class ViewExportTests(unittest.TestCase):
    def run_helper(self, path, current=None, current_name='Viewport: 1'):
        host = ModuleType('abaqus')
        host.session = SimpleNamespace(currentViewportName=current_name,
                                       viewports={'Viewport: 1': current})
        host.getInput = lambda prompt, suggested: path
        with patch.dict('sys.modules', {'abaqus': host}), contextlib.redirect_stdout(io.StringIO()):
            return runpy.run_path(str(SCRIPT), run_name='__main__')

    def test_nonuniform_and_root_camera_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '相机 视角.json'
            self.run_helper(str(path), viewport())
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['deformation'], {'mode': 'NONUNIFORM',
                             'factors': [1., 1., 10.], 'source': 'Abaqus viewport'})
            self.assertEqual(data['cameraPosition'], [1., 2., 3.])
            self.assertEqual(data['projection'], 'PERSPECTIVE')
            self.assertEqual(data['width'], 100.)

    def test_uniform_reads_only_uniform_scale(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'camera.json'
            self.run_helper(str(path), viewport('UNIFORM', 10))
            deformation = json.loads(path.read_text())['deformation']
            self.assertEqual(deformation['mode'], 'UNIFORM')
            self.assertEqual(deformation['factors'], [10., 10., 10.])

    def test_auto_saves_computed_scale(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'camera.json'
            self.run_helper(str(path), viewport('AUTO', 2.75))
            deformation = json.loads(path.read_text())['deformation']
            self.assertEqual(deformation['mode'], 'AUTO')
            self.assertEqual(deformation['factors'], [2.75] * 3)

    def test_cancel_never_reads_viewport_or_writes(self):
        self.run_helper(None, current_name='missing')
        self.run_helper('', current_name='missing')

    def test_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'camera.json'
            path.write_bytes(b'existing user file')
            with self.assertRaisesRegex(RuntimeError, 'export failed'):
                self.run_helper(str(path), viewport())
            self.assertEqual(path.read_bytes(), b'existing user file')

    def test_absent_odb_or_model_view_fails_without_file(self):
        for displayed in (None, SimpleNamespace(name='Part-1')):
            with self.subTest(displayed=displayed), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'camera.json'
                current = viewport()
                current.displayedObject = displayed
                with self.assertRaisesRegex(RuntimeError, 'must display an ODB'):
                    self.run_helper(str(path), current)
                self.assertFalse(path.exists())

    def test_missing_mode_property_gives_host_error_without_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'camera.json'
            current = viewport('UNIFORM', 10)
            del current.odbDisplay.commonOptions.uniformScaleFactor
            with self.assertRaisesRegex(RuntimeError, 'export failed.*uniformScaleFactor'):
                self.run_helper(str(path), current)
            self.assertFalse(path.exists())

    def test_invalid_modes_and_nonfinite_scales_leave_no_file(self):
        cases = [('UNKNOWN', 1), ('NONUNIFORM', (1, 2)),
                 ('NONUNIFORM', (1, 1, float('nan'))),
                 ('UNIFORM', float('inf')), ('AUTO', float('-inf'))]
        for mode, value in cases:
            with self.subTest(mode=mode, value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'camera.json'
                with self.assertRaisesRegex(RuntimeError, 'export failed'):
                    self.run_helper(str(path), viewport(mode, value))
                self.assertFalse(path.exists())

    def test_nonfinite_camera_is_rejected_before_creating_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'camera.json'
            current = viewport()
            current.view.width = float('nan')
            with self.assertRaisesRegex(RuntimeError, 'export failed'):
                self.run_helper(str(path), current)
            self.assertFalse(path.exists())

    def test_failed_write_removes_only_new_incomplete_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'camera.json'
            real_open = open

            class InterruptedWrite:
                def __init__(self, *args, **kwargs):
                    self.handle = real_open(*args, **kwargs)

                def __enter__(self):
                    return self

                def write(self, text):
                    self.handle.write(text[:10])
                    raise OSError('Simulated disk write failure')

                def __exit__(self, *args):
                    self.handle.close()

            def fail_json_write(filename, mode='r', *args, **kwargs):
                if str(filename) == str(path) and mode == 'x':
                    return InterruptedWrite(filename, mode, *args, **kwargs)
                return real_open(filename, mode, *args, **kwargs)

            with patch('builtins.open', side_effect=fail_json_write):
                with self.assertRaisesRegex(RuntimeError, 'Simulated disk write failure'):
                    self.run_helper(str(path), viewport())
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
