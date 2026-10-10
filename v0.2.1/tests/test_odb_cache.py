import copy
import json
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"backend"))
import odb_cache as cache


def eps_text(name="model.odb", step="Step-2", increment=2298, time="200.0"):
    return "%!PS-Adobe-3.0 EPSF-3.0\n(ODB: {}    Abaqus/Standard 2024) show\n(Step: {}) show\n(Increment   {}: Step Time = {}) show\n".format(name, step, increment, time)


def mesh(path, displacement=1.0, frame_index=1800, increment=2298):
    return {"schema": "abaqus-source-mesh-1.8", "metadata": {
        "source_odb": str(Path(path).resolve()), "source_size_bytes": Path(path).stat().st_size,
        "step_name": "Step-2", "frame_index": frame_index, "increment_number": increment,
        "frame_value": 200.0, "read_only": True, "matching_frame_indices": [frame_index]},
        "instances": [{"name": "PART-1", "nodes": [[1, 0., 0., 0.], [2, 1., 0., 0.]],
                       "elements": [{"label": 1, "type": "T3D2", "connectivity": [1, 2]}],
                       "displacements": [[1, displacement, 0., 0.], [2, displacement, 0., 0.]]}]}


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(__file__).resolve().parent/("_test_cache_"+uuid.uuid4().hex)
        self.folder.mkdir()
        self.odb = self.folder/"model.odb"; self.odb.write_bytes(b"fakeODB")
        self.eps = self.folder/"plot_frame999.eps"; self.eps.write_text(eps_text(), encoding="latin1")
        self.cache = self.folder/"cache with spaces"

    def tearDown(self):
        assert self.folder.resolve().parent == Path(__file__).resolve().parent
        shutil.rmtree(self.folder)

    def load(self, **kwargs):
        return cache.load_frame(self.odb, self.eps, self.cache, **kwargs)

    def test_eps_annotation_authoritative_not_filename_hint(self):
        labels = cache.read_eps_metadata(self.eps)
        self.assertEqual(labels["filename_frame_hint"], 999)
        self.assertEqual(labels["increment_number"], 2298)
        self.assertEqual(labels["step_name"], "Step-2")
        self.assertEqual(labels["odb_basename"], "model.odb")

    def test_primary_variable_and_step_time_without_increment(self):
        self.eps.write_text('%!PS\n(Step: Loading) show\n(Step Time = 2.000e+02) show\n(Primary Var: S, Mises) show\n')
        labels = cache.read_eps_metadata(self.eps)
        self.assertEqual(labels['primary_variable'], 'S, Mises')
        self.assertEqual(labels['step_time'], 200.)
        self.assertIsNone(labels['increment_number'])
        self.assertIsNone(labels['odb_basename'])

    def test_escaped_ps_names_binary_eps_and_comments(self):
        import struct
        ps = eps_text(name=r"part\(A\).odb", step=r"Step\0552").encode("latin1")
        ps += b"% (ODB: wrong.odb) (Step: Wrong)\n"
        self.eps.write_bytes(b"\xc5\xd0\xd3\xc6"+struct.pack("<II", 30, len(ps))+b"\0"*18+ps+b"preview")
        labels = cache.read_eps_metadata(self.eps)
        self.assertEqual(labels["odb_basename"], "part(A).odb")
        self.assertEqual(labels["step_name"], "Step-2")

    def test_missing_or_conflicting_labels_fail(self):
        for text in ("%!PS\n(ODB: model.odb) show", eps_text()+"(Step: Different) show"):
            self.eps.write_text(text)
            with self.assertRaises(cache.MetadataMismatchError): cache.read_eps_metadata(self.eps)

    def test_wrong_odb_and_explicit_step_fail_before_launcher(self):
        with patch.object(cache, "discover_abaqus", side_effect=AssertionError("must not launch")):
            with self.assertRaises(cache.MetadataMismatchError): self.load(step_name="Wrong")
            self.eps.write_text(eps_text(name="different.odb"))
            with self.assertRaises(cache.MetadataMismatchError): self.load()

    def test_model_identity_uses_all_coordinates_connectivity_and_types_not_U(self):
        original = mesh(self.odb)
        for field in ("coordinate", "connectivity", "type"):
            changed = copy.deepcopy(original)
            if field == "coordinate": changed["instances"][0]["nodes"][1][1] += .001
            if field == "connectivity": changed["instances"][0]["elements"][0]["connectivity"] = [2, 1]
            if field == "type": changed["instances"][0]["elements"][0]["type"] = "B31"
            self.assertNotEqual(cache.model_hash(original), cache.model_hash(changed))
        changed = mesh(self.odb, displacement=99)
        self.assertEqual(cache.model_hash(original), cache.model_hash(changed))

    def test_cache_hit_never_discovers_or_opens_odb(self):
        with patch.object(cache, "discover_abaqus", return_value=["fake"]), patch.object(cache, "_run_extractor", return_value=mesh(self.odb)) as run:
            first = self.load()
            self.assertEqual(run.call_count, 1)
        with patch.object(cache, "discover_abaqus", side_effect=AssertionError("offline")), patch.object(cache, "_run_extractor", side_effect=AssertionError("no ODB open")):
            second = self.load(abaqus_command="not-installed")
        self.assertFalse(first["frame_cache_hit"])
        self.assertTrue(second["frame_cache_hit"])
        self.assertEqual(first["mesh_path"], second["mesh_path"])
        self.assertEqual(cache._read_json(second["mesh_path"])["instances"][0]["displacements"][0][1], 1.)

    def test_another_odb_shares_model_but_gets_own_U(self):
        with patch.object(cache, "discover_abaqus", return_value=["fake"]), patch.object(cache, "_run_extractor", return_value=mesh(self.odb)):
            first = self.load()
        other = self.folder/"other.odb"; other.write_bytes(b"differentfile")
        self.eps.write_text(eps_text(name=other.name))
        with patch.object(cache, "discover_abaqus", return_value=["fake"]), patch.object(cache, "_run_extractor", return_value=mesh(other, displacement=2.)):
            second = cache.load_frame(other, self.eps, self.cache)
        self.assertTrue(second["model_cache_hit"])
        self.assertFalse(second["frame_cache_hit"])
        self.assertEqual(first["model_hash"], second["model_hash"])
        self.assertNotEqual(first["mesh_path"], second["mesh_path"])
        self.assertEqual(cache._read_json(second["mesh_path"])["instances"][0]["displacements"][0][1], 2.)

    def test_changed_odb_stat_does_not_reuse_old_frame(self):
        with patch.object(cache, "discover_abaqus", return_value=["fake"]), patch.object(cache, "_run_extractor", side_effect=lambda *args: mesh(self.odb)) as run:
            self.load(); self.odb.write_bytes(b"changedODBsize"); result = self.load()
        self.assertEqual(run.call_count, 2)
        self.assertFalse(result["frame_cache_hit"])

    def test_changed_increment_or_wrong_frame_does_not_reuse_hit(self):
        with patch.object(cache, "discover_abaqus", return_value=["fake"]), patch.object(cache, "_run_extractor", return_value=mesh(self.odb)):
            self.load()
            with self.assertRaises(cache.MetadataMismatchError): self.load(frame_index=1799)
            self.eps.write_text(eps_text(increment=2333))
            with self.assertRaises(cache.MetadataMismatchError): self.load()

    def test_corrupt_U_cache_is_not_silently_used(self):
        with patch.object(cache, "discover_abaqus", return_value=["fake"]), patch.object(cache, "_run_extractor", return_value=mesh(self.odb)) as run:
            self.load()
            file = next((self.cache/"frames").glob("*.framecache"))
            data = cache._read_json(file)
            data["payload"]["displacements"][0]["displacements"][0][1] = 100
            cache._atomic_json(file, data)
            result = self.load()
        self.assertEqual(run.call_count, 2)
        self.assertFalse(result["frame_cache_hit"])

    def test_source_changes_during_extraction_cannot_commit_frame(self):
        def run(*args):
            data = mesh(self.odb); self.odb.write_bytes(b"concurrentchange"); return data
        with patch.object(cache, "discover_abaqus", return_value=["fake"]), patch.object(cache, "_run_extractor", side_effect=run):
            with self.assertRaisesRegex(RuntimeError, "changed during read"): self.load()
        self.assertFalse((self.cache/"frames").exists())

    def test_cancel_before_start_has_no_side_effect(self):
        with self.assertRaises(cache.CancelledError): self.load(cancel=lambda: True)
        self.assertFalse(self.cache.exists())

    def test_path_with_spaces_is_single_argv_element(self):
        launcher = self.folder/"launch helper.py"
        launcher.write_text("import json,pathlib,sys\nr=json.loads(pathlib.Path(sys.argv[-1]).read_text())\npathlib.Path(r['output_path']).write_text(json.dumps({'request':r,'argv':sys.argv}))\n")
        expected = cache.read_eps_metadata(self.eps)
        result = cache._run_extractor(self.odb, expected, None, self.cache, [sys.executable, str(launcher)], None, None)
        self.assertEqual(result["request"]["expected"], expected)
        self.assertEqual(result["argv"][1], "python")
        self.assertEqual(result["argv"][-2], "--request")
        self.assertEqual(result["argv"][-1], 'request.json')
        self.assertEqual(result["argv"][-3], 'extract_odb.py')

    def test_launcher_path_is_not_shell_split(self):
        with patch.object(cache.shutil, "which", return_value=r"C:\Program Files\Abaqus\abaqus.bat"):
            self.assertEqual(cache.discover_abaqus(r"C:\Program Files\Abaqus\abaqus.bat"), [r"C:\Program Files\Abaqus\abaqus.bat"])

    @unittest.skipUnless(os.name == "nt", "Windows owned-process-tree regression")
    def test_cancellation_terminates_only_launched_dummy_process_tree(self):
        launcher = self.folder/"sleep launcher.py"
        launcher.write_text(
            "import json,pathlib,sys,subprocess,time,os\n"
            "r=json.loads(pathlib.Path(sys.argv[-1]).read_text())\n"
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
            "pathlib.Path(r['status_path']).write_text(json.dumps({'stage':'dummy','message':'waiting','current':0,'total':1,'owned_child_pid':p.pid}))\n"
            "time.sleep(30)\n")
        events = []; started = time.monotonic()
        with self.assertRaises(cache.CancelledError):
            cache._run_extractor(self.odb, cache.read_eps_metadata(self.eps), None, self.cache,
                                 [sys.executable, str(launcher)], events.append,
                                 lambda: time.monotonic()-started > .7)
        self.assertLess(time.monotonic()-started, 10)
        child = next(e['owned_child_pid'] for e in events if 'owned_child_pid' in e)
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x00100000, False, child)  # SYNCHRONIZE only
        if handle:
            try:
                kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                self.assertEqual(kernel.WaitForSingleObject(handle, 1000), 0)
            finally:
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel.CloseHandle(handle)


if __name__ == "__main__": unittest.main()
