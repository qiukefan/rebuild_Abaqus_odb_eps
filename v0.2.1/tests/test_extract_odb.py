from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"backend"))
from extract_odb import select_frame, extract


def odb(frames):
    return types.SimpleNamespace(steps={"Step-2": types.SimpleNamespace(frames=[
        types.SimpleNamespace(incrementNumber=increment, frameValue=time) for increment, time in frames])})


class FrameSelectionTests(unittest.TestCase):
    def setUp(self):
        self.expected = {"step_name": "Step-2", "increment_number": 2298,
                         "step_time": 200., "step_time_text": "200.0", "time_tolerance": .050000001}

    def test_unique_metadata_selects_frame_not_filename_index(self):
        source = odb([(0, 0), (2297, 199.9), (2298, 200.)])
        frame, index, count, matches = select_frame(source, self.expected)
        self.assertEqual((index, count, matches), (2, 3, [2]))

    def test_time_only_requires_unique_frame(self):
        self.expected['increment_number'] = None
        self.assertEqual(select_frame(odb([(1, 0), (2, 200.)]), self.expected)[1], 1)
        with self.assertRaisesRegex(ValueError, 'identifies 2 frames'):
            select_frame(odb([(1, 199.99), (2, 200.)]), self.expected)

    def test_explicit_wrong_frame_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not match EPS"):
            select_frame(odb([(2297, 200.), (2298, 200.)]), self.expected, 0)

    def test_wrong_time_or_increment_rejected(self):
        for frames in ([(2298, 201.)], [(2297, 200.)]):
            with self.assertRaisesRegex(ValueError, "identifies 0 frames"):
                select_frame(odb(frames), self.expected)

    def test_ambiguous_frames_require_explicit_valid_selection(self):
        source = odb([(2298, 200.), (2298, 200.)])
        with self.assertRaisesRegex(ValueError, "identifies 2 frames"):
            select_frame(source, self.expected)
        self.assertEqual(select_frame(source, self.expected, 1)[1], 1)

    def test_missing_step_is_not_substituted(self):
        self.expected["step_name"] = "Absent"
        with self.assertRaisesRegex(ValueError, "absent"): select_frame(odb([(2298, 200.)]), self.expected)

    def test_extract_opens_read_only_reads_only_selected_U_and_keeps_all_elements(self):
        class HeaderOnlyFrame:
            incrementNumber = 1
            frameValue = 0.
            @property
            def fieldOutputs(self):
                raise AssertionError("An unselected frame's field was accessed")
        part = types.SimpleNamespace(name="PART-1")
        values = [types.SimpleNamespace(instance=part, nodeLabel=i, position="NODAL", data=(float(i), 0., 0.)) for i in (1, 2)]
        selected = types.SimpleNamespace(incrementNumber=2298, frameValue=200.,
                                         fieldOutputs={"U": types.SimpleNamespace(values=values)})
        instance = types.SimpleNamespace(
            nodes=[types.SimpleNamespace(label=i, coordinates=(float(i), 0., 0.)) for i in (1, 2)],
            elements=[types.SimpleNamespace(label=1, type="T3D2", connectivity=(1, 2)),
                      types.SimpleNamespace(label=2, type="B31", connectivity=(1, 2))])
        source = types.SimpleNamespace(steps={"Step-2": types.SimpleNamespace(frames=[HeaderOnlyFrame(), selected])},
                                       rootAssembly=types.SimpleNamespace(instances={"PART-1": instance}), close=Mock())
        opener = Mock(return_value=source)
        expected = dict(self.expected, odb_basename=Path(__file__).name)
        result = extract({"odb_path": __file__, "expected": expected, "frame_index": None}, open_odb=opener)
        opener.assert_called_once_with(path=str(Path(__file__).resolve()), readOnly=True)
        source.close.assert_called_once()
        self.assertEqual(result["metadata"]["frame_index"], 1)
        self.assertEqual(result["metadata"]["element_type_counts"], {"T3D2": 1, "B31": 1})
        self.assertEqual(len(result["instances"][0]["elements"]), 2)


if __name__ == "__main__": unittest.main()
