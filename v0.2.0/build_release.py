"""Build the complete Windows package, excluding user models and caches."""
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

from build_portable_runtime import validate_sources

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
VERSION = "0.2.0"
PUBLIC_FILES = (
    "LICENSE", "README.md", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md",
    "requirements.txt", "SurfaceMeshRebuild_v0.2.0.jsx",
    "test-surface-mesh-v0.2.0.cjs", "表面网格重建_v0.2.0_使用说明.txt",
    "docs/PUBLISHING.zh-CN.md", "docs/RELEASE_v0.2.0.md",
    "v0.2.0/launch_job.vbs", "v0.2.0/ExportCurrentView_v0.2.0.py",
    "v0.2.0/验证记录_v0.2.0.txt", "v0.2.0/build_portable_runtime.py",
    "v0.2.0/build_release.py", "v0.2.0/resources/camera_seed.json",
    "v0.2.0/tests/test_build_portable_runtime.py",
    "v0.2.0/tests/test_extract_odb.py", "v0.2.0/tests/test_odb_cache.py",
    "v0.2.0/tests/test_template_reuse.py", "v0.2.0/tests/test_worker_and_camera.py",
)


def _distributable(path, parent):
    relative = path.relative_to(parent)
    if not path.is_file() or path.suffix.lower() in {".pyc", ".pyo"}:
        return False
    if "__pycache__" in relative.parts or "site-packages" in relative.parts:
        return False
    notice = path.name.lower().startswith(("license", "licence", "copying", "copyright", "notice"))
    notice = notice or any(part.lower() in {"licenses", "licences"} for part in relative.parts[:-1])
    return notice or not any(part in {"test", "tests", "benchmarks"} for part in relative.parts)


def collect_files(root=ROOT):
    root = Path(root).resolve()
    base = root / "v0.2.0"
    selected = [root / name for name in PUBLIC_FILES]
    for path in selected:
        if not path.is_file():
            raise ValueError("Required public file is missing: " + str(path))
    backend = list((base / "backend").glob("*.py"))
    if not (base / "backend" / "worker.py").is_file():
        raise ValueError("The geometry backend is missing")
    selected.extend(backend)
    # Reuse the portable builder's binary/ABI checks without copying anything.
    # A temporary, empty target avoids overwriting or trusting an old package.
    with tempfile.TemporaryDirectory(prefix="surface-mesh-preflight-") as temp:
        runtime, dependencies, _ = validate_sources(base / "runtime", base / "_deps", Path(temp))
    for folder in [runtime] + dependencies:
        selected.extend(path for path in folder.rglob("*") if _distributable(path, folder))
    return sorted(set(selected))


def build(root=ROOT):
    root = Path(root).resolve()
    selected = collect_files(root)  # Missing runtime must fail before touching the ZIP.
    archive = root / "SurfaceMeshRebuild-v0.2.0-windows-x64.zip"
    manifest = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in selected}
    with tempfile.TemporaryDirectory(prefix=".release-build-", dir=root) as temp:
        staged = Path(temp) / archive.name
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
            for path in selected:
                output.write(path, path.relative_to(root).as_posix())
            output.writestr("v0.2.0/package-manifest.json", json.dumps(
                {"version": VERSION, "files": manifest}, indent=2, ensure_ascii=False))
        with zipfile.ZipFile(staged) as output:
            bad = output.testzip()
            if bad:
                raise RuntimeError("Corrupt archive entry: " + bad)
        staged.replace(archive)
    return {"archive": str(archive), "bytes": archive.stat().st_size,
            "files": len(selected), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}


if __name__ == "__main__":
    try:
        result = build()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit("Build failed: " + str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
