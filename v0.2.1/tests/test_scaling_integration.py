"""Scale parsing and XYZ deformation across metadata, worker, and templates.

Only registration/visibility boundaries are mocked. Metadata comes from real
PostScript labels and the mesh is a complete, synthetic C3D20 cube.
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE/'backend'))
sys.path.insert(0, str(PACKAGE/'_deps'))
import numpy as np
from fit_projection import parse_eps
from odb_cache import read_eps_metadata
from template_reuse import create_template
from worker import Job


def cube_mesh():
    corners = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0),
               (0, 0, 2), (2, 0, 2), (2, 2, 2), (0, 2, 2)]
    pairs = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6),
             (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
    points = corners + [tuple((corners[a][i]+corners[b][i])/2 for i in range(3)) for a, b in pairs]
    return {'metadata': {'deformationScale': 99}, 'instances': [{
        'name': 'SyntheticCube', 'nodes': [[i, *p] for i, p in enumerate(points, 1)],
        'displacements': [[i, .01*i, -.02*i, .03*i] for i in range(1, 21)],
        'elements': [{'label': 1, 'type': 'C3D20', 'connectivity': list(range(1, 21))}]}]}


def write_eps(path, scale):
    path.write_text('%!PS-Adobe-3.0 EPSF-3.0\n1 1 scale\n'
                    '(Step: Step-2) show\n(Increment 2298: Step Time = 200.0) show\n'
                    '(Primary Var: PEMAG) show\n'
                    '(Deformed Var: U   Deformation Scale Factor: '+scale+') show\n', encoding='ascii')


def document_for(eps):
    frame = '0 0 500 500 rectfill\n'
    return SimpleNamespace(source_sha256=hashlib.sha256(eps.read_bytes()).hexdigest(),
                           removable_strokes=[SimpleNamespace(index=7)], black_strokes=[],
                           mesh_bounds_pt=(0, 0, 500, 500), ps=frame, viewport_start=0,
                           overlay_offset=len(frame), overlay_matrix=(1., 0., 0., 1., 0., 0.))


def verified_fit(document, deformation=None):
    fit = {'accepted': True, 'sourceEPSsha256': document.source_sha256,
           'strokeMatches': [{'blackStrokeIndex': 7}],
           'validation': {'matchedNodes': 100, 'nodeRmsPt': .0001}}
    if deformation is not None:
        fit['deformation'] = deformation
    return fit


class StopAtDiscretization(Exception):
    """The test checks input coordinates without exercising hidden-line removal."""


class ScalingIntegrationTests(unittest.TestCase):
    def assert_world_coordinates(self, world, factors):
        instance = cube_mesh()['instances'][0]
        self.assertEqual(set(world), set(range(1, 21)))
        for row, displacement in zip(instance['nodes'], instance['displacements']):
            label, x, y, z = row
            _, ux, uy, uz = displacement
            expected = [x+factors[0]*ux, y+factors[1]*uy, z+factors[2]*uz]
            np.testing.assert_allclose(world[label], expected, rtol=0, atol=1e-12)

    def test_metadata_and_fit_parser_agree_on_current_eps_scales(self):
        with tempfile.TemporaryDirectory() as directory:
            eps = Path(directory)/'frame.eps'
            for label, mode, factors in (
                    ('+1.000e+01', 'UNIFORM', [10., 10., 10.]),
                    ('x = +1.000e+00 y = +1.000e+00 z = +1.000e+01', 'NONUNIFORM', [1., 1., 10.])):
                with self.subTest(label=label):
                    write_eps(eps, label)
                    metadata = read_eps_metadata(eps)
                    with patch('fit_projection.read_eps', return_value=document_for(eps)):
                        _, deformation, digest = parse_eps(eps)
                    self.assertEqual(metadata['deformation'], deformation)
                    self.assertEqual(deformation['mode'], mode)
                    self.assertEqual(deformation['factors'], factors)
                    self.assertEqual(digest, hashlib.sha256(eps.read_bytes()).hexdigest())
                    self.assertEqual(metadata['deformed_variable'], 'U')

    def test_odb_worker_uses_current_xyz_scales_despite_stale_camera_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            eps, odb, mesh_path = folder/'frame.eps', folder/'model.odb', folder/'mesh.json'
            write_eps(eps, 'x=1 y=1 z=10')
            odb.write_bytes(b'synthetic ODB placeholder; loader is mocked')
            mesh_path.write_text(json.dumps(cube_mesh()), encoding='utf-8')
            document = document_for(eps)
            frame = dict(mesh_path=str(mesh_path), model_hash='synthetic', model_cache_hit=True,
                         frame_cache_hit=True, metadata={'deformationScale': 99})
            camera = dict(cameraPosition=[0, 0, 20], cameraTarget=[0, 0, 0],
                          cameraUpVector=[0, 1, 0], deformationScale=99,
                          deformation={'mode': 'UNIFORM', 'factors': [99, 99, 99]})
            camera_path = folder/'camera.json'
            camera_path.write_text(json.dumps(camera), encoding='utf-8')
            captured = {}

            def registered_fit(eps_path, source_mesh, candidate_path):
                # Geometry registration is outside this test, but scale parsing
                # is real and must remain independent of the saved camera value.
                self.assertEqual(json.loads(Path(candidate_path).read_text())['deformationScale'], 99)
                _, deformation, _ = parse_eps(eps_path)
                fit = verified_fit(document, deformation)
                fit['deformationScale'] = 99  # New per-axis data must win.
                return fit

            def capture_coordinates(faces, edges, coordinates, project, subdivisions):
                self.assertEqual((len(faces), len(edges)), (6, 12))
                captured.update(coordinates)
                raise StopAtDiscretization()

            job = Job(folder/'request.json')
            job.request = dict(epsPath=str(eps), odbPath=str(odb), cacheDir=str(folder/'cache'),
                               outputDir=str(folder/'output'), cameraPath=str(camera_path))
            with patch('odb_cache.load_frame', return_value=frame), \
                 patch('camera_sources.discover', return_value=[(camera, 'synthetic camera JSON')]), \
                 patch('eps_io.read_eps', return_value=document), \
                 patch('fit_projection.read_eps', return_value=document), \
                 patch('fit_projection.fit_projection', side_effect=registered_fit), \
                 patch('surface_visibility.discretize', side_effect=capture_coordinates):
                with self.assertRaises(StopAtDiscretization):
                    job.run_odb()
            self.assert_world_coordinates(captured, [1, 1, 10])
            self.assertEqual(json.loads((folder/'fit.json').read_text())['deformation']['factors'], [1., 1., 10.])

    def test_template_projects_scaled_c3d20_nodes_and_records_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            eps, mesh_path, fit_path = folder/'frame.eps', folder/'mesh.json', folder/'fit.json'
            mesh_path.write_text(json.dumps(cube_mesh()), encoding='utf-8')
            for label, factors, legacy in (('x=1 y=1 z=10', [1, 1, 10], False), ('3', [3, 3, 3], True)):
                with self.subTest(legacy=legacy):
                    write_eps(eps, label)
                    document = document_for(eps)
                    deformation = read_eps_metadata(eps)['deformation']
                    fit = verified_fit(document, None if legacy else deformation)
                    fit['deformationScale'] = 3 if legacy else 99
                    fit_path.write_text(json.dumps(fit), encoding='utf-8')
                    captured = {}

                    def capture_projection(world, current_fit):
                        captured.update({i: row.copy() for i, row in enumerate(world, 1)})
                        return world[:, :2].copy(), -np.ones(len(world))

                    output = folder/('legacy.meshcache.json' if legacy else 'xyz.meshcache.json')
                    with patch('template_reuse.read_eps', return_value=document), \
                         patch('template_reuse.project_world', side_effect=capture_projection):
                        create_template(eps, mesh_path, fit_path, output)
                    self.assert_world_coordinates(captured, factors)
                    saved = json.loads(output.read_text(encoding='utf-8'))
                    self.assertEqual((len(saved['faces']), len(saved['edges'])), (6, 12))
                    self.assertEqual(saved['referenceDeformation']['factors'], factors)
                    self.assertEqual(saved['referenceDeformation']['mode'], 'UNIFORM' if legacy else 'NONUNIFORM')
                    if not legacy:
                        self.assertEqual(saved['referenceDeformation'], deformation)
                    for node, x, y in saved['referenceNodeXY']:
                        np.testing.assert_allclose([x, y], captured[node][:2], rtol=0, atol=1e-12)


if __name__ == '__main__':
    unittest.main()
