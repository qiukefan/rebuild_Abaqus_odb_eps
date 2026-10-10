import json
from pathlib import Path
import sys
import tempfile
import unittest
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'backend'))
import template_reuse as tr

class TemplateTests(unittest.TestCase):
    def test_wrong_template_version_rejects_before_geometry(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'model.meshcache.json';p.write_text('{"schema":"wrong"}')
            with self.assertRaisesRegex(ValueError, '模板格式'):
                tr.rebuild_from_template('absent.eps',p,d)
            self.assertEqual(list(Path(d).glob('*.eps')),[])

    def test_cancel_never_opens_inputs(self):
        with self.assertRaisesRegex(ValueError,'取消'):
            tr.rebuild_from_template('absent.eps','absent.json','absent',cancel=lambda:True)

    def test_internal_face_gap_cannot_be_invented(self):
        with self.assertRaisesRegex(ValueError,'内部断开'):
            tr._join_clip_gap(tr.np.array([2.,2.]),tr.np.array([3.,3.]),[0.,0.,10.,10.])

    def test_opposite_clip_sides_cannot_be_guessed(self):
        with self.assertRaisesRegex(ValueError,'裁剪归属'):
            tr._join_clip_gap(tr.np.array([0.,2.]),tr.np.array([10.,3.]),[0.,0.,10.,10.])

    def test_real_cross_condition_reuse_has_no_extra_surface_edges(self):
        comparison=BASE/'tests/template-results/comparison.json'
        if not comparison.exists(): self.skipTest('Private integration fixtures are not distributed with public source')
        for item in json.loads(comparison.read_text()):
            self.assertEqual(item['extraEdges'],[])
            # EPS depicts linear half-edges; reference ODB uses curved subdivision.
            self.assertLess(item['hausdorffPt'],.1)
            self.assertLess(abs(item['lengthTemplate']-item['lengthODB']),.2)

if __name__=='__main__':unittest.main()
