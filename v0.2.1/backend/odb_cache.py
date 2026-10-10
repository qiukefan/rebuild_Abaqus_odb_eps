"""Verified EPS-to-ODB frame selection with split model and frame caches.

Cache hits require the same absolute ODB path, size and mtime_ns. Models may be
shared only after hashing every original node and element (including all types).
The Abaqus helper uses openOdb(readOnly=True); no solver or Viewer is launched.
"""
from __future__ import annotations
import hashlib
import json
import math
import ntpath
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import uuid


class MetadataMismatchError(ValueError):
    pass


class CancelledError(RuntimeError):
    pass


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf8")


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("."+path.name+"."+uuid.uuid4().hex+".tmp")
    try:
        temporary.write_bytes(_canonical(value))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _check_cancel(cancel):
    if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
        raise CancelledError("ODB read cancelled")


def _emit(progress, stage, message, current=0, total=1, **extra):
    if progress:
        progress(dict(stage=stage, message=message, current=current, total=total, **extra))


def _ps_strings(ps):
    """Read escaped PostScript literal strings, not comments or raw regex text."""
    i, size = 0, len(ps)
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}
    while i < size:
        if ps[i] == "%":
            while i < size and ps[i] not in "\r\n": i += 1
        elif ps[i] == "(":
            i += 1; depth, text = 1, []
            while i < size and depth:
                char = ps[i]; i += 1
                if char == "\\":
                    if i >= size: raise MetadataMismatchError("Truncated EPS string escape")
                    char = ps[i]; i += 1
                    if char in "01234567":
                        digits = char
                        while i < size and len(digits) < 3 and ps[i] in "01234567":
                            digits += ps[i]; i += 1
                        text.append(chr(int(digits, 8) & 255))
                    elif char in "\r\n":
                        if char == "\r" and i < size and ps[i] == "\n": i += 1
                    else: text.append(escapes.get(char, char))
                elif char == "(": depth += 1; text.append(char)
                elif char == ")":
                    depth -= 1
                    if depth: text.append(char)
                else: text.append(char)
            if depth: raise MetadataMismatchError("Unterminated EPS literal string")
            yield "".join(text)
        else:
            i += 1


def read_eps_metadata(eps_path):
    """Read displayed Step/Time; optional ODB/Increment add cross-checks."""
    eps_path = Path(eps_path).resolve()
    # Metadata needs no geometry scan. This reads the declared DOS EPS PS slice
    # and leaves both the binary source and preview untouched.
    raw = eps_path.read_bytes()
    source_digest = hashlib.sha256(raw).hexdigest()
    if raw.startswith(b"\xc5\xd0\xd3\xc6"):
        if len(raw) < 30: raise MetadataMismatchError("Truncated binary EPS header")
        offset, length = struct.unpack_from("<II", raw, 4)
        if offset < 30 or length <= 0 or offset+length > len(raw):
            raise MetadataMismatchError("Invalid EPS PostScript slice")
        raw = raw[offset:offset+length]
    elif raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    if not raw.startswith(b"%!"):
        raise MetadataMismatchError("Selected file has no EPS PostScript header")
    labels = list(_ps_strings(raw.decode("latin1")))
    from deformation import read_deformation_from_labels
    deformation = read_deformation_from_labels(labels, required=False)
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    odbs, steps, frames, times, variables, deform_vars = set(), set(), set(), set(), set(), set()
    for label in labels:
        match = re.match(r"^\s*ODB:\s*(.*?\.odb)(?:\s|$)", label, re.I)
        if match: odbs.add(ntpath.basename(match.group(1).strip().replace("/", "\\")))
        match = re.fullmatch(r"\s*Step:\s*(.*?)\s*", label)
        if match: steps.add(match.group(1))
        match = re.search(r"\bIncrement\s*:?\s*(\d+)\s*:\s*Step\s+Time\s*[=:]\s*("+number+r")", label, re.I)
        if match: frames.add((int(match.group(1)), match.group(2)))
        match = re.search(r"\bStep\s+Time\s*[=:]\s*("+number+r")", label, re.I)
        if match: times.add(match.group(1))
        match = re.fullmatch(r"\s*Primary\s+Var:\s*(.*?)\s*", label, re.I)
        if match: variables.add(match.group(1))
        match = re.search(r"\bDeformed\s+Var:\s*(.*?)\s+Deformation Scale Factor:", label, re.I)
        if match: deform_vars.add(match.group(1))
    if len(odbs) > 1 or len(steps) != 1 or len(times) != 1 or len(frames) > 1 or len(variables) > 1 or len(deform_vars) > 1:
        raise MetadataMismatchError("EPS 标注不完整或含多个视图：必须能唯一确定 Step 和 Step Time；ODB、增量和变量标注也不能冲突。")
    increment, time_text = next(iter(frames)) if frames else (None, next(iter(times)))
    value = float(time_text)
    if not math.isfinite(value): raise MetadataMismatchError("Non-finite EPS Step Time")
    mantissa, _, exponent = time_text.lower().partition("e")
    decimals = len(mantissa.split(".", 1)[1]) if "." in mantissa else 0
    resolution = 10.0**(int(exponent or "0")-decimals)
    hint = re.search(r"(?:^|[_-])frame(\d+)(?:[_\-.]|$)", eps_path.name, re.I)
    return {"odb_basename": next(iter(odbs)) if odbs else None, "step_name": next(iter(steps)),
            "increment_number": increment, "step_time": value, "step_time_text": time_text,
            "primary_variable": next(iter(variables)) if variables else None,
            "deformed_variable": next(iter(deform_vars)) if deform_vars else None,
            "deformation": deformation,
            "time_tolerance": max(abs(value)*1e-12, resolution*0.500000001, 1e-10),
            "filename_frame_hint": int(hint.group(1)) if hint else None,
            "eps_path": str(eps_path), "eps_sha256": source_digest}


def _verify_eps_unchanged(expected):
    if hashlib.sha256(Path(expected["eps_path"]).read_bytes()).hexdigest() != expected["eps_sha256"]:
        raise RuntimeError("EPS changed during frame selection; select the inputs again")


def _fingerprint(path):
    stat = path.stat()
    return {"path": os.path.normcase(str(path.resolve())), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _matches(metadata, expected, frame_index=None):
    return (metadata.get("step_name") == expected["step_name"]
            and (expected["increment_number"] is None or metadata.get("increment_number") == expected["increment_number"])
            and abs(float(metadata.get("frame_value", float("inf")))-expected["step_time"]) <= expected["time_tolerance"]
            and (frame_index is None or metadata.get("frame_index") == frame_index))


def model_payload(mesh):
    """Canonical full original model identity, independent of ODB filename/U."""
    instances = []
    for item in sorted(mesh["instances"], key=lambda item: item["name"]):
        instances.append({"name": item["name"], "nodes": sorted(item["nodes"], key=lambda n: n[0]),
                          "elements": sorted([{"label": e["label"], "type": e["type"],
                                               "connectivity": e["connectivity"]} for e in item["elements"]],
                                             key=lambda e: e["label"])})
    return {"schema": "abaqus-original-model-0.2", "instances": instances}


def model_hash(mesh):
    return _hash(model_payload(mesh))


def discover_abaqus(abaqus_command=None):
    if abaqus_command is not None:
        command = [os.fspath(abaqus_command)] if isinstance(abaqus_command, (str, os.PathLike)) else list(abaqus_command)
        if not command or not all(isinstance(arg, str) and arg for arg in command):
            raise ValueError("abaqus_command must be a launcher path or nonempty argument list")
        executable = shutil.which(command[0]) or (str(Path(command[0]).resolve()) if Path(command[0]).is_file() else None)
        if not executable: raise FileNotFoundError("Abaqus launcher not found: "+command[0])
        return [executable]+command[1:]
    for name in ("abaqus", "abq2024"):
        executable = shutil.which(name)
        if executable: return [executable]
    raise FileNotFoundError("No Abaqus Python launcher found; install/configure abaqus or abq2024, or pass abaqus_command")


def _terminate_owned_tree(process):
    if process.poll() is not None: return
    if os.name == "nt":
        # The owned wrapper is a member of its own kill-on-close Job Object.
        # Killing that wrapper closes its sole job handle and kills descendants,
        # without process enumeration or any global Abaqus process-name matching.
        if getattr(process, "_owns_windows_job", False):
            process.terminate()
        else:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    try: process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=5)


def _windows_own_job():
    """Bind THIS helper before it creates any descendants (no assignment race).

    Microsoft Job Objects documentation:
    https://learn.microsoft.com/windows/win32/procthread/job-objects
    The unnamed, non-inherited handle remains open until this helper exits.
    """
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                       "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.AssignProcessToJobObject.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle: raise ctypes.WinError(ctypes.get_last_error())
    limits = Extended(); limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        code = ctypes.get_last_error(); kernel.CloseHandle(handle); raise ctypes.WinError(code)
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        code = ctypes.get_last_error(); kernel.CloseHandle(handle); raise ctypes.WinError(code)
    return handle


def _owned_launcher(request_path):
    request = _read_json(request_path)
    job_handle = _windows_own_job() if os.name == "nt" else None
    # Keep job_handle open through process exit: closing it early would also
    # terminate this wrapper before its child return code can be propagated.
    return subprocess.call(request["argv"], shell=False, cwd=request["cwd"],
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)


def _run_extractor(odb_path, expected, frame_index, cache_dir, command, progress, cancel):
    job = cache_dir/"jobs"/uuid.uuid4().hex
    job.mkdir(parents=True)
    request_path, output_path, status_path = (job/name for name in ("request.json", "extraction.json", "status.json"))
    _atomic_json(request_path, {"odb_path": str(odb_path), "expected": expected, "frame_index": frame_index,
                                "output_path": str(output_path), "status_path": str(status_path)})
    # Abaqus's Windows entry point is a batch file. Give it ASCII relative
    # arguments so %, &, ! etc. in user folder names never become batch syntax.
    # All source and output paths remain literal JSON data.
    shutil.copy2(Path(__file__).with_name('extract_odb.py'), job/'extract_odb.py')
    argv = command+["python", "extract_odb.py", "--request", "request.json"]
    if os.name == "nt":
        launch_request = job/"launch.json"
        _atomic_json(launch_request, {"argv": argv, "cwd": str(job)})
        argv = [sys.executable, str(Path(__file__).resolve()), "--owned-launch", str(launch_request)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    _check_cancel(cancel)
    with (job/"abaqus.log").open("wb") as log:
        process = subprocess.Popen(argv, shell=False, cwd=str(job), stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=flags, start_new_session=os.name != "nt")
        process._owns_windows_job = os.name == "nt"
        last_status = None
        try:
            while process.poll() is None:
                _check_cancel(cancel)
                try:
                    status = _read_json(status_path)
                    if status != last_status:
                        if progress: progress(status)
                        last_status = status
                except (OSError, ValueError): pass
                time.sleep(0.10)
            _check_cancel(cancel)
        except BaseException:
            _terminate_owned_tree(process)
            raise
    if process.returncode != 0 or not output_path.is_file():
        try: detail = _read_json(status_path).get("message", "")
        except (OSError, ValueError): detail = ""
        if not detail:
            detail = (job/"abaqus.log").read_text(encoding="utf8", errors="replace")[-3000:]
        raise RuntimeError("Abaqus read-only extraction failed (exit {}): {}. Log: {}".format(process.returncode, detail, job/"abaqus.log"))
    result = _read_json(output_path)
    output_path.unlink()  # Large transient copy; compact status/log/request remain.
    return result


def _resolve_mesh(cache_dir, key, model, frame):
    displacements = {item["name"]: item["displacements"] for item in frame["displacements"]}
    instances = []
    for item in model["instances"]:
        counts = {}
        for element in item["elements"]: counts[element["type"]] = counts.get(element["type"], 0)+1
        u = displacements[item["name"]]
        if [row[0] for row in u] != [node[0] for node in item["nodes"]]:
            raise ValueError("Frame U labels do not exactly match the cached original model")
        instances.append(dict(item, displacements=u, node_count=len(item["nodes"]),
                              element_type_counts=counts, exported_element_count=len(item["elements"])))
    full = {"schema": "abaqus-source-mesh-1.8", "metadata": frame["metadata"], "instances": instances}
    path = cache_dir/"resolved"/(key+".mesh.json")
    encoded = _canonical(full)
    if not path.is_file() or path.read_bytes() != encoded: _atomic_json(path, full)
    return path


def _read_cached(cache_dir, entry, fingerprint, expected, frame_index):
    key = entry["frame_key"]
    if not re.fullmatch(r"[0-9a-f]{64}", key): raise ValueError("Invalid frame cache key")
    record = _read_json(cache_dir/"frames"/(key+".framecache"))
    frame, digest = record["payload"], record["sha256"]
    if _hash(frame) != digest or frame["odb_fingerprint"] != fingerprint:
        raise ValueError("Frame cache integrity/provenance mismatch")
    metadata = frame["metadata"]
    if not _matches(metadata, expected, frame_index): raise ValueError("Cached frame does not match EPS")
    if frame_index is None and expected['increment_number'] is None:
        criteria = {k:expected[k] for k in ('step_name','increment_number','step_time','time_tolerance')}
        if metadata.get('selection_criteria') != criteria:
            raise ValueError('Time-only EPS needs its own unambiguous frame verification')
    if frame_index is None and metadata.get("matching_frame_indices") != [metadata["frame_index"]]:
        raise MetadataMismatchError("EPS selection is ambiguous; specify a verified frame index")
    identity = frame["model_hash"]
    if not re.fullmatch(r"[0-9a-f]{64}", identity): raise ValueError("Invalid model cache hash")
    model = _read_json(cache_dir/"models"/(identity+".meshcache"))
    if _hash(model) != identity: raise ValueError("Model cache integrity mismatch")
    if _hash({"odb": fingerprint, "step": metadata["step_name"], "frame": metadata["frame_index"]}) != key:
        raise ValueError("Frame cache key does not match its metadata")
    path = _resolve_mesh(cache_dir, key, model, frame)
    return path, identity, metadata


def load_frame(odb_path, eps_path, cache_dir, abaqus_command=None, step_name=None,
               frame_index=None, progress=None, cancel=None):
    """Return a verified legacy-compatible mesh JSON; EPS remains authoritative.

    progress receives dict(stage,message,current,total); cancel is a callable
    or Event. A frame cache hit never discovers/starts Abaqus or opens the ODB.
    """
    _check_cancel(cancel)
    odb_path, cache_dir = Path(odb_path).resolve(), Path(cache_dir).resolve()
    if not odb_path.is_file() or odb_path.suffix.lower() != ".odb":
        raise FileNotFoundError("Select an existing .odb file: "+str(odb_path))
    expected = read_eps_metadata(eps_path)
    if expected["odb_basename"] and odb_path.name.casefold() != expected["odb_basename"].casefold():
        raise MetadataMismatchError("Selected ODB {!r} differs from EPS annotation {!r}".format(odb_path.name, expected["odb_basename"]))
    if step_name is not None and step_name != expected["step_name"]:
        raise MetadataMismatchError("Explicit step_name differs from the EPS displayed Step")
    if frame_index is not None and (type(frame_index) is not int or frame_index < 0):
        raise ValueError("frame_index must be a nonnegative integer")
    fingerprint = _fingerprint(odb_path); catalog_path = cache_dir/"catalogs"/(_hash(fingerprint)+".json")
    try: catalog = _read_json(catalog_path)
    except (OSError, ValueError): catalog = {"odb_fingerprint": fingerprint, "entries": []}
    if catalog.get("odb_fingerprint") != fingerprint: catalog = {"odb_fingerprint": fingerprint, "entries": []}
    _emit(progress, "cache.lookup", "Checking verified frame cache")
    for entry in catalog["entries"]:
        if not _matches(entry.get("metadata", {}), expected, frame_index): continue
        _check_cancel(cancel)
        try:
            path, identity, metadata = _read_cached(cache_dir, entry, fingerprint, expected, frame_index)
        except MetadataMismatchError: raise
        except (OSError, ValueError, KeyError, TypeError): continue
        _check_cancel(cancel)
        _verify_eps_unchanged(expected)
        _emit(progress, "cache.hit", "Verified frame cache hit; Abaqus was not started", 1, 1)
        return {"mesh_path": str(path), "model_hash": identity, "frame_cache_hit": True,
                "model_cache_hit": True, "metadata": dict(metadata, eps_metadata=expected)}
    _emit(progress, "cache.miss", "Reading selected ODB and verifying original model")
    command = discover_abaqus(abaqus_command)
    mesh = _run_extractor(odb_path, expected, frame_index, cache_dir, command, progress, cancel)
    _check_cancel(cancel)
    if _fingerprint(odb_path) != fingerprint:
        raise RuntimeError("ODB size/mtime changed during read; no frame cache was committed")
    _verify_eps_unchanged(expected)
    metadata = mesh["metadata"]
    if not _matches(metadata, expected, frame_index) or not metadata.get("read_only"):
        raise MetadataMismatchError("Extractor returned a frame inconsistent with EPS labels")
    if frame_index is None and metadata.get("matching_frame_indices") != [metadata.get("frame_index")]:
        raise MetadataMismatchError("Extractor did not establish a unique EPS-matched frame")
    if os.path.normcase(str(Path(metadata["source_odb"]).resolve())) != fingerprint["path"]:
        raise MetadataMismatchError("Extractor source path differs from selected ODB")
    model = model_payload(mesh); identity = _hash(model)
    model_path = cache_dir/"models"/(identity+".meshcache")
    try: model_hit = _hash(_read_json(model_path)) == identity
    except (OSError, ValueError): model_hit = False
    if not model_hit: _atomic_json(model_path, model)
    frame = {"schema": "abaqus-frame-U-0.2", "odb_fingerprint": fingerprint, "model_hash": identity,
             "metadata": metadata,
             "displacements": [{"name": item["name"], "displacements": sorted(item["displacements"], key=lambda u: u[0])}
                               for item in sorted(mesh["instances"], key=lambda item: item["name"])]}
    key = _hash({"odb": fingerprint, "step": metadata["step_name"], "frame": metadata["frame_index"]})
    _check_cancel(cancel)
    _atomic_json(cache_dir/"frames"/(key+".framecache"), {"payload": frame, "sha256": _hash(frame)})
    path = _resolve_mesh(cache_dir, key, model, frame)
    catalog["entries"] = [entry for entry in catalog["entries"] if entry.get("frame_key") != key]
    catalog["entries"].append({"frame_key": key, "metadata": {k: metadata[k] for k in ("step_name", "frame_index", "increment_number", "frame_value")}})
    _atomic_json(catalog_path, catalog)
    _emit(progress, "cache.saved", "Verified model and frame cached", 1, 1, model_cache_hit=model_hit)
    return {"mesh_path": str(path), "model_hash": identity, "frame_cache_hit": False,
            "model_cache_hit": model_hit, "metadata": dict(metadata, eps_metadata=expected)}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--owned-launch":
        raise SystemExit("Internal launcher usage: odb_cache.py --owned-launch request.json")
    raise SystemExit(_owned_launcher(sys.argv[2]))
