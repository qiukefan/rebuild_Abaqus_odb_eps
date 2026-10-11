"""Failure boundaries and literal-only camera discovery."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE/'backend'))
from worker import Job, atomic_json
from camera_sources import cameras_in_text, discover

class CameraTests(unittest.TestCase):
    def test_balanced_tuples_and_incremental_values(self):
        text = '''session.viewports['Viewport: 1'].view.setValues(cameraPosition=(1,2,3), cameraTarget=(0,0,0))
session.viewports['Viewport: 1'].view.setValues(cameraUpVector=(0,1,0), projection=PERSPECTIVE)'''
        cameras = cameras_in_text(text)
        self.assertEqual(len(cameras), 1)
        self.assertEqual(cameras[0]['cameraPosition'], (1,2,3))
        self.assertEqual(cameras[0]['projection'], 'PERSPECTIVE')

    def test_replay_code_is_never_executed(self):
        text = "v.view.setValues(cameraPosition=__import__('os').system('bad'), cameraTarget=(0,0,0), cameraUpVector=(0,1,0))"
        with patch('os.system', side_effect=AssertionError('executed')):
            self.assertEqual(cameras_in_text(text), [])

    def test_invalid_explicit_camera_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'bad.json'; p.write_text('{}')
            with self.assertRaisesRegex(ValueError, '相机配置'):
                discover(Path(d)/'image.eps', explicit_path=p)

    def test_saved_view_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            camera = dict(cameraPosition=[1,2,3], cameraTarget=[0,0,0], cameraUpVector=[0,1,0])
            p=Path(d)/'view.json'; p.write_text(json.dumps({'savedView':camera,'ignore':'x'}))
            candidates=discover(Path(d)/'image.eps',explicit_path=p)
            self.assertEqual(candidates[0][0]['cameraPosition'], [1,2,3])
            self.assertEqual(sum(x[0]['cameraPosition']==[1,2,3] for x in candidates), 1)

class WorkerTests(unittest.TestCase):
    def test_progress_copy_io_failure_cannot_reverse_published_success(self):
        with tempfile.TemporaryDirectory() as d:
            job = Job(Path(d)/'request.json')
            with patch.object(job, 'load_request'), \
                    patch.object(job, 'run_template', return_value={'outputEPS':'result.eps'}), \
                    patch('worker.write_progress', side_effect=OSError('progress copy cannot be written')):
                job.request = {'mode':'template'}
                self.assertEqual(job.run(), 0)
            self.assertEqual(json.loads((Path(d)/'terminal.json').read_text(encoding='utf-8'))['state'], 'done')

    def test_locked_error_log_does_not_hide_the_actual_failure(self):
        with tempfile.TemporaryDirectory() as d:
            job = Job(Path(d)/'request.json')
            with patch.object(job, 'load_request', side_effect=ValueError('actual extraction failure')), \
                    patch.object(Path, 'write_text', side_effect=PermissionError('log is locked')):
                self.assertEqual(job.run(), 1)
            final = json.loads((Path(d)/'terminal.json').read_text(encoding='utf-8'))
            self.assertEqual(final['state'], 'error')
            self.assertEqual(final['message'], 'actual extraction failure')

    def test_final_state_is_delivered_when_progress_file_cannot_refresh(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            atomic_json(directory/'status.json', {'state':'running', 'progress':10})
            job = Job(directory/'request.json')
            with patch('worker.write_progress', return_value=False):
                job.status('finished', 100, state='done', outputEPS='result.eps')
            final = json.loads((directory/'terminal.json').read_text(encoding='utf-8'))
            self.assertEqual(final['state'], 'done')
            self.assertEqual(final['outputEPS'], 'result.eps')
            self.assertEqual(json.loads((directory/'status.json').read_text())['state'], 'running')

    def test_terminal_publish_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as d:
            job = Job(Path(d)/'request.json')
            with patch('worker.atomic_json', side_effect=OSError('disk full')), \
                    patch('worker.write_progress') as progress:
                with self.assertRaisesRegex(OSError, 'disk full'):
                    job.status('finished', 100, state='done')
            progress.assert_not_called()

    def test_template_result_version_can_be_added_to_done_status(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'request.json'
            job=Job(p); job.status('done',100,state='done',version='0.2.2',outputEPS='result.eps')
            self.assertEqual(json.loads((p.parent/'status.json').read_text(encoding='utf-8'))['state'],'done')

    def test_error_is_machine_readable_in_chinese_path(self):
        with tempfile.TemporaryDirectory(prefix='网格 空格 & ') as d:
            p=Path(d)/'request.json'; atomic_json(p, {'version':'bad'})
            self.assertEqual(Job(p).run(), 1)
            status=json.loads((p.parent/'status.json').read_text(encoding='utf-8'))
            self.assertEqual(status['state'], 'error')
            self.assertIn('版本', status['message'])
            self.assertTrue((p.parent/'error.log').exists())

    def test_cancel_before_work(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'request.json'; atomic_json(p, {})
            (p.parent/'cancel.flag').touch()
            self.assertEqual(Job(p).run(), 1)
            self.assertEqual(json.loads((p.parent/'status.json').read_text(encoding='utf-8'))['state'], 'cancelled')

    def test_job_directory_binding_and_source_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'request.json'; source=Path(d)/'original.eps'; source.write_bytes(b'original')
            atomic_json(p, {'version':'0.2.2','mode':'odb','epsPath':str(source),'jobDir':str(Path(d)/'other')})
            self.assertEqual(Job(p).run(), 1)
            self.assertEqual(source.read_bytes(), b'original')

    def test_output_names_never_reuse_source(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'request.json'; source=Path(d)/'original.eps'; source.write_bytes(b'original')
            atomic_json(p, {'version':'0.2.2','mode':'odb','epsPath':str(source),'cacheDir':str(Path(d)/'cache'),'outputDir':d})
            job=Job(p); job.load_request()
            a,b=job.new_output(),job.new_output()
            self.assertNotEqual(a,b); self.assertNotEqual(a,source)

if __name__=='__main__': unittest.main()
