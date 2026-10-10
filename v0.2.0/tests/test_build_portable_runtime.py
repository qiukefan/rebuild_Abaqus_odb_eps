"""Portable builder tests use only temporary fake runtime/dependency fixtures."""

import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("portable_builder", Path(__file__).resolve().parents[1] / "build_portable_runtime.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class PortableBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.python = self.root / "Python 3.12"
        self.deps = self.root / "deps"
        self.output = self.root / "output"
        self.python.mkdir()
        self.deps.mkdir()
        executable = bytearray(70)
        executable[:2] = b"MZ"
        struct.pack_into("<I", executable, 60, 64)
        executable[64:70] = b"PE\0\0\x64\x86"
        for name in builder.RUNTIME_FILES:
            (self.python / name).write_bytes(b"Python license" if name == "LICENSE.txt" else executable)
        (self.python / "Lib" / "encodings").mkdir(parents=True)
        (self.python / "Lib" / "encodings" / "__init__.py").write_text("# encodings")
        (self.python / "DLLs").mkdir()
        for name in builder.DEPENDENCIES:
            (self.deps / name).mkdir()
            (self.deps / name / "__init__.py").write_text("# package")
            (self.deps / (name + ".libs")).mkdir()
            (self.deps / (name + ".libs") / "LICENSE.txt").write_text("bundled DLL notice")
            dist_info = self.deps / (name + "-1.0.dist-info")
            (dist_info / "licenses").mkdir(parents=True)
            (dist_info / "METADATA").write_text("Name: " + name)
            (dist_info / "WHEEL").write_text("Tag: cp312-cp312-win_amd64\n")
            (dist_info / "licenses" / "LICENSE").write_text("dependency license")

    def test_copies_only_requested_dependencies_and_preserves_notices(self):
        (self.deps / "private_package").mkdir()
        (self.deps / "private_package" / "secrets.py").write_text("secret")
        tests = self.deps / "numpy" / "tests"
        tests.mkdir()
        (tests / "test_large.py").write_text("# test")
        (tests / "LICENSE.txt").write_text("fixture license")
        cache = self.deps / "numpy" / "__pycache__"
        cache.mkdir()
        (cache / "__init__.pyc").write_bytes(b"bytecode")
        installed = self.python / "Lib" / "site-packages"
        installed.mkdir()
        (installed / "LICENSE-private.txt").write_text("unrelated")
        builder.build(self.python, self.deps, self.output)
        self.assertEqual((self.output / "runtime" / "LICENSE.txt").read_text(), "Python license")
        self.assertEqual((self.output / "_deps" / "numpy" / "tests" / "LICENSE.txt").read_text(), "fixture license")
        self.assertFalse((self.output / "_deps" / "numpy" / "tests" / "test_large.py").exists())
        self.assertFalse((self.output / "_deps" / "numpy" / "__pycache__").exists())
        self.assertFalse((self.output / "runtime" / "Lib" / "site-packages").exists())
        self.assertFalse((self.output / "_deps" / "private_package").exists())
        for name in builder.DEPENDENCIES:
            self.assertTrue((self.output / "_deps" / (name + ".libs") / "LICENSE.txt").is_file())
            self.assertTrue((self.output / "_deps" / (name + "-1.0.dist-info") / "licenses" / "LICENSE").is_file())

    def test_refuses_existing_output_without_changing_it(self):
        (self.output / "runtime").mkdir(parents=True)
        marker = self.output / "runtime" / "keep.txt"
        marker.write_text("original")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            builder.build(self.python, self.deps, self.output)
        self.assertEqual(marker.read_text(), "original")
        self.assertFalse((self.output / "_deps").exists())

    def test_rejects_overlapping_sources_before_writes(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            builder.build(self.python, self.deps, self.python / "subfolder")
        self.assertFalse((self.python / "subfolder").exists())

    def test_rejects_non_x64_runtime(self):
        (self.python / "python.exe").write_bytes(b"not a Windows executable")
        with self.assertRaisesRegex(ValueError, "x64"):
            builder.build(self.python, self.deps, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_incomplete_python_installation(self):
        (self.python / "python312.dll").unlink()
        with self.assertRaisesRegex(ValueError, "python312.dll"):
            builder.build(self.python, self.deps, self.output)
        self.assertFalse(self.output.exists())

    def test_requires_all_three_dependency_libraries(self):
        (self.deps / "shapely.libs" / "LICENSE.txt").unlink()
        (self.deps / "shapely.libs").rmdir()
        with self.assertRaisesRegex(ValueError, "shapely.libs"):
            builder.build(self.python, self.deps, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_ambiguous_dependency_versions(self):
        (self.deps / "numpy-2.0.dist-info").mkdir()
        with self.assertRaisesRegex(ValueError, "exactly one numpy"):
            builder.build(self.python, self.deps, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_incompatible_wheel(self):
        (self.deps / "numpy-1.0.dist-info" / "WHEEL").write_text("Tag: cp313-cp313-win_amd64\n")
        with self.assertRaisesRegex(ValueError, "compatible wheel"):
            builder.build(self.python, self.deps, self.output)
        self.assertFalse(self.output.exists())

    def test_accepts_compatible_stable_abi_wheel(self):
        (self.deps / "shapely-1.0.dist-info" / "WHEEL").write_text("Tag: cp310-abi3-win_amd64\n")
        builder.build(self.python, self.deps, self.output)
        self.assertTrue((self.output / "runtime" / "python.exe").is_file())

    def test_command_line_requires_explicit_sources(self):
        with self.assertRaises(SystemExit) as error:
            builder.main([])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
