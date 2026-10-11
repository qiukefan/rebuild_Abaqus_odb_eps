"""Annotation parsing boundaries, without private ODB or EPS fixtures."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"backend"))
from deformation import factors_from_fit, read_deformation_from_labels, read_eps_deformation


PREFIX = "Deformed Var: U   Deformation Scale Factor: "


class DeformationLabelsTests(unittest.TestCase):
    def test_user_uniform_annotation(self):
        label = PREFIX + "+1.000e+01"
        result = read_deformation_from_labels([label])
        self.assertEqual(result, {"mode": "UNIFORM", "factors": [10., 10., 10.],
                                  "annotation": label, "source": "EPS annotation"})

    def test_user_nonuniform_annotation(self):
        result = read_deformation_from_labels([PREFIX + "x = +1.000e+00  y = +1.000e+00  z = +1.000e+01"])
        self.assertEqual(result["mode"], "NONUNIFORM")
        self.assertEqual(result["factors"], [1., 1., 10.])

    def test_case_whitespace_axis_order_negative_and_zero(self):
        result = read_deformation_from_labels([
            " DEFORMED VAR: U\tdeformation \n scale\t factor : Z = -2E+1\nX=0\tY=+.5  "])
        self.assertEqual(result["factors"], [0., .5, -20.])
        self.assertEqual(result["mode"], "NONUNIFORM")

    def test_finite_uniform_numbers(self):
        for text, expected in (("-3.0e-2", -.03), ("0", 0.), (".5", .5), ("1.", 1.)):
            with self.subTest(text=text):
                self.assertEqual(read_deformation_from_labels([PREFIX + text])["factors"], [expected]*3)

    def test_identical_repeated_labels_with_different_number_format(self):
        result = read_deformation_from_labels([PREFIX + "2", PREFIX + "+2.000E+00"])
        self.assertEqual(result["factors"], [2., 2., 2.])
        result = read_deformation_from_labels([PREFIX + "x=1 y=2 z=3", PREFIX + "z=3. x=1.0 y=2e0"])
        self.assertEqual(result["factors"], [1., 2., 3.])

    def test_conflicting_annotations_or_modes_fail(self):
        for a, b in (("1", "2"), ("x=1 y=1 z=2", "x=1 y=1 z=3"), ("1", "x=1 y=1 z=1")):
            with self.subTest(a=a, b=b), self.assertRaisesRegex(ValueError, "冲突"):
                read_deformation_from_labels([PREFIX+a, PREFIX+b])

    def test_missing_optional_is_none_but_required_fails(self):
        self.assertIsNone(read_deformation_from_labels(["Step: Step-2"], required=False))
        with self.assertRaisesRegex(ValueError, "缺少"):
            read_deformation_from_labels(["Step: Step-2"])

    def test_malformed_annotation_never_uses_partial_match(self):
        invalid = ["", "1,1,2", "1 1 2", "1 extra", "1e", "1e+", "--1", "1_000",
                   "x=1 y=2", "x=1 y=2 y=3", "x=1 y=2 z=3 w=4", "x=1 y=2 z=3 junk",
                   "x=1,y=2,z=3", "NaN", "+Infinity", "1e309", "x=1 y=1 z=nan",
                   "x=1 y=1 z=1e309", "[1, 1, 2]", "1 Deformation Scale Factor: 2"]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                read_deformation_from_labels([PREFIX+value], required=False)
        with self.assertRaises(ValueError):
            read_deformation_from_labels(["Deformation Scale Factor = 1"])

    def test_non_string_labels_fail(self):
        with self.assertRaises(ValueError):
            read_deformation_from_labels([None], required=False)


class EpsDeformationTests(unittest.TestCase):
    def parse_bytes(self, raw):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"plot.eps"
            path.write_bytes(raw)
            return read_eps_deformation(path)

    def test_dos_payload_excludes_comments_and_preview(self):
        ps = ("%!PS-Adobe-3.0 EPSF-3.0\n% ("+PREFIX+"999) show\n("+PREFIX+"x=1 y=1 z=10) show\n").encode("ascii")
        header = b"\xc5\xd0\xd3\xc6"+struct.pack("<II", 30, len(ps))+b"\0"*18
        preview = ("("+PREFIX+"123) show").encode("ascii")
        self.assertEqual(self.parse_bytes(header+ps+preview)["factors"], [1., 1., 10.])

    def test_ps_escapes_and_line_continuations(self):
        raw = (b"%!PS\n(Deformed Var: U\\(shown\\)  Deformation Scale Factor: "
               b"\\170=\\061 y=\\061 z=+1.\\\n000e+01) show\n")
        result = self.parse_bytes(raw)
        self.assertEqual(result["factors"], [1., 1., 10.])
        self.assertIn("U(shown)", result["annotation"])

    def test_raw_comment_cannot_supply_missing_factor(self):
        with self.assertRaises(ValueError):
            self.parse_bytes(("%!PS\n% ("+PREFIX+"10)\n(Step: Step-2) show").encode("ascii"))

    def test_utf8_bom_and_repeated_display_annotation(self):
        ps = ("%!PS\n("+PREFIX+"10) show\n("+PREFIX+"1e1) show\n").encode("ascii")
        self.assertEqual(self.parse_bytes(b"\xef\xbb\xbf"+ps)["factors"], [10.]*3)

    def test_invalid_payload_and_unterminated_strings_fail(self):
        for raw in (b"not EPS", b"\xc5\xd0\xd3\xc6", b"%!PS\n(Deformation Scale Factor: 1"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.parse_bytes(raw)


class FitFactorsTests(unittest.TestCase):
    def test_new_factors_take_precedence_over_legacy_scalar(self):
        fit = {"deformation": {"mode": "NONUNIFORM", "factors": [1, 0, -10]}, "deformationScale": 99}
        self.assertEqual(factors_from_fit(fit), (1., 0., -10.))
        self.assertEqual(factors_from_fit({"deformation": {"mode": "UNIFORM", "factors": (2, 2., 2)}}), (2.,)*3)

    def test_old_scalar_is_compatible(self):
        for scale in (0, -1, 10.5):
            self.assertEqual(factors_from_fit({"deformationScale": scale}), (float(scale),)*3)

    def test_invalid_new_factors_never_fall_back(self):
        invalid = [None, 1, [], {}, {"factors": [1, 1, 1]},
                   {"mode": "invalid", "factors": [1, 1, 1]},
                   {"mode": "UNIFORM", "factors": [1, 1, 2]},
                   {"mode": "NONUNIFORM", "factors": [1, 2]},
                   {"mode": "NONUNIFORM", "factors": [1, 2, 3, 4]},
                   {"mode": "NONUNIFORM", "factors": "123"},
                   {"mode": "NONUNIFORM", "factors": [1, 1, "2"]},
                   {"mode": "NONUNIFORM", "factors": [1, 1, True]},
                   {"mode": "NONUNIFORM", "factors": [1, 1, None]},
                   {"mode": "NONUNIFORM", "factors": [1, 1, float("nan")]},
                   {"mode": "NONUNIFORM", "factors": [1, 1, float("inf")]},
                   {"mode": "NONUNIFORM", "factors": [1, 1, 10**400]}]
        for deformation in invalid:
            with self.subTest(deformation=deformation), self.assertRaises(ValueError):
                factors_from_fit({"deformation": deformation, "deformationScale": 1})

    def test_invalid_legacy_scalar_and_missing_fit_fail(self):
        for fit in (None, [], {}, {"deformationScale": None}, {"deformationScale": "1"},
                    {"deformationScale": [1, 1, 2]}, {"deformationScale": True},
                    {"deformationScale": float("nan")}, {"deformationScale": float("inf")},
                    {"deformationScale": 10**400}):
            with self.subTest(fit=fit), self.assertRaises(ValueError):
                factors_from_fit(fit)


if __name__ == "__main__":
    unittest.main()
