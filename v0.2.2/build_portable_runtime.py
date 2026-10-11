"""Copy user-supplied Windows CPython 3.12 x64 and numerical dependencies.

Does not download or install anything. Example (all paths are user supplied):

    python build_portable_runtime.py --python-root C:/Python312 --deps-root C:/build/mesh-deps --output-dir C:/build/mesh-package

The output directory may contain application source, but its runtime and _deps
directories must not exist. Existing releases are never overwritten. CPython's
LICENSE.txt and dependency license notices are retained.
"""

import argparse
import re
import shutil
import struct
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
RUNTIME_FILES = (
    "python.exe", "pythonw.exe", "python312.dll", "python3.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt",
)
DEPENDENCIES = ("numpy", "scipy", "shapely")
EXCLUDED_DIRECTORIES = {
    "__pycache__", "site-packages", "test", "tests", "benchmarks",
    "idlelib", "tkinter", "turtledemo",
}


def _overlap(first, second):
    return first == second or first in second.parents or second in first.parents


def _check_x64_pe(path):
    """Reject non-Windows and 32-bit/ARM binaries without executing them."""
    try:
        with path.open("rb") as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                raise ValueError("missing DOS header")
            offset = struct.unpack_from("<I", header, 60)[0]
            stream.seek(offset)
            pe_header = stream.read(6)
        if pe_header != b"PE\0\0\x64\x86":
            raise ValueError("expected Windows x64 PE machine 0x8664")
    except (OSError, ValueError, struct.error) as error:
        raise ValueError("Not a Windows x64 binary: {} ({})".format(path, error)) from error


def _supported_wheel_tag(tag):
    parts = tag.split("-")
    if len(parts) != 3:
        return False
    python_tag, abi_tag, platform_tag = parts
    if platform_tag not in ("win_amd64", "any"):
        return False
    if python_tag in ("py3", "py312") and abi_tag == "none":
        return True
    if python_tag == "cp312" and abi_tag in ("cp312", "abi3", "none"):
        return True
    stable_abi = re.fullmatch(r"cp3(\d+)", python_tag)
    return bool(stable_abi and abi_tag == "abi3" and int(stable_abi.group(1)) <= 12)


def validate_sources(python_root, deps_root, output_dir):
    """Validate everything before creating or changing any output files."""
    python_root = Path(python_root).expanduser().resolve()
    deps_root = Path(deps_root).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    targets = (output_dir / "runtime", output_dir / "_deps")
    for source in (python_root, deps_root):
        if not source.is_dir():
            raise ValueError("Source directory does not exist: {}".format(source))
        for target in targets:
            if _overlap(source, target):
                raise ValueError("Source and output must not overlap: {} / {}".format(source, target))
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("Output must be a directory: {}".format(output_dir))
    for target in targets:
        if target.exists() or target.is_symlink():
            raise ValueError("Refusing to overwrite existing directory: {}. Use a new --output-dir.".format(target))
    for filename in RUNTIME_FILES:
        path = python_root / filename
        if not path.is_file():
            raise ValueError("Required CPython 3.12 runtime file is missing: {}".format(path))
        if path.suffix.lower() in (".exe", ".dll"):
            _check_x64_pe(path)
    for directory in ("Lib", "DLLs"):
        if not (python_root / directory).is_dir():
            raise ValueError("A complete CPython installation is required; missing: {}".format(python_root / directory))
    if not (python_root / "Lib" / "encodings" / "__init__.py").is_file():
        raise ValueError("CPython standard library is incomplete: missing Lib/encodings/__init__.py")
    dependency_directories = []
    for name in DEPENDENCIES:
        for directory in (deps_root / name, deps_root / (name + ".libs")):
            if not directory.is_dir():
                raise ValueError("Required dependency directory is missing: {}".format(directory))
            dependency_directories.append(directory)
        metadata_dirs = sorted(path for path in deps_root.glob(name + "-*.dist-info") if path.is_dir())
        if len(metadata_dirs) != 1:
            raise ValueError("Expected exactly one {}-*.dist-info directory, found {}".format(name, len(metadata_dirs)))
        metadata_dir = metadata_dirs[0]
        for filename in ("METADATA", "WHEEL"):
            if not (metadata_dir / filename).is_file():
                raise ValueError("Required dependency metadata is missing: {}".format(metadata_dir / filename))
        wheel = (metadata_dir / "WHEEL").read_text(encoding="utf-8")
        tags = [line[5:].strip() for line in wheel.splitlines() if line.startswith("Tag: ")]
        if not any(_supported_wheel_tag(tag) for tag in tags):
            raise ValueError("{} is not a CPython 3.12 Windows x64 compatible wheel: {}".format(name, tags))
        dependency_directories.append(metadata_dir)
    return python_root, dependency_directories, output_dir


def _copy_ignore(directory, names):
    root = Path(directory)
    return [name for name in names if
            (name in EXCLUDED_DIRECTORIES and (root / name).is_dir())
            or name.lower().endswith((".pyc", ".pyo"))]


def _copy_tree(source, target):
    shutil.copytree(source, target, ignore=_copy_ignore)
    # Some license notices live beside test fixtures. Preserve those notices,
    # even when their surrounding test or benchmark code is intentionally absent.
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if "site-packages" in relative.parts:
            continue  # Never include unrelated installed packages.
        name = path.name.lower()
        is_notice = name.startswith(("license", "licence", "copying", "copyright", "notice"))
        in_licenses_directory = any(part.lower() in ("licenses", "licences") for part in relative.parts[:-1])
        if path.is_file() and (is_notice or in_licenses_directory):
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def build(python_root, deps_root, output_dir=HERE):
    python_root, dependency_directories, output_dir = validate_sources(python_root, deps_root, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".portable-build-", dir=output_dir) as temporary:
        staging = Path(temporary)
        runtime = staging / "runtime"
        runtime.mkdir()
        for filename in RUNTIME_FILES:
            shutil.copy2(python_root / filename, runtime / filename)
        for directory in ("Lib", "DLLs"):
            _copy_tree(python_root / directory, runtime / directory)
        dependencies = staging / "_deps"
        dependencies.mkdir()
        for directory in dependency_directories:
            _copy_tree(directory, dependencies / directory.name)
        # Finish both copies before publishing either directory to the app.
        published = []
        try:
            for directory in (runtime, dependencies):
                target = output_dir / directory.name
                if target.exists() or target.is_symlink():
                    raise ValueError("Output appeared during build; refusing to overwrite: {}".format(target))
                directory.rename(target)
                published.append(target)
        except BaseException:
            for target in reversed(published):
                target.rename(staging / target.name)
            raise
    return output_dir / "runtime"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--python-root", required=True, type=Path, help="Complete Windows x64 CPython 3.12 installation (contains Lib and DLLs)")
    parser.add_argument("--deps-root", required=True, type=Path, help="Preinstalled NumPy, SciPy and Shapely, including .libs and .dist-info")
    parser.add_argument("--output-dir", type=Path, default=HERE, help="Application package directory; defaults to this script's directory")
    args = parser.parse_args(argv)
    try:
        runtime = build(args.python_root, args.deps_root, args.output_dir)
    except (OSError, ValueError) as error:
        parser.exit(1, "Build failed: {}\n".format(error))
    print("Portable runtime ready:", runtime)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
