"""Abaqus Python 3.10 helper: read one verified ODB frame, never run a job.

The only command-line payload is --request /absolute/request.json. All paths,
expected EPS labels and output locations are read from that request file.
"""
import argparse
import collections
import json
import math
import os
import sys
import time
import uuid


from atomic_io import atomic_json, write_progress


def time_matches(actual, expected):
    # EPS time text is rounded. Its last displayed digit defines the narrow
    # admissible interval; increment number and step must still match exactly.
    return abs(float(actual)-float(expected["step_time"])) <= float(expected["time_tolerance"])


def select_frame(odb, expected, explicit_index=None):
    step_name = expected["step_name"]
    if step_name not in odb.steps:
        raise ValueError("EPS step {!r} is absent from the ODB".format(step_name))
    frames = odb.steps[step_name].frames
    matches = [i for i, frame in enumerate(frames)
               if (expected["increment_number"] is None or int(frame.incrementNumber) == int(expected["increment_number"]))
               and time_matches(frame.frameValue, expected)]
    if explicit_index is not None:
        if type(explicit_index) is not int or not 0 <= explicit_index < len(frames):
            raise ValueError("Requested frame index is outside EPS step {!r}".format(step_name))
        if explicit_index not in matches:
            frame = frames[explicit_index]
            raise ValueError("Requested frame {} does not match EPS: ODB increment={}, time={}; EPS increment={}, time={}".format(
                explicit_index, frame.incrementNumber, frame.frameValue,
                expected["increment_number"], expected["step_time_text"]))
        index = explicit_index
    else:
        if len(matches) != 1:
            raise ValueError("EPS step/time/increment identifies {} frames {}; choose a verified frame explicitly".format(len(matches), matches[:12]))
        index = matches[0]
    return frames[index], index, len(frames), matches


def extract(request, open_odb=None, status=None):
    if open_odb is None:
        from odbAccess import openOdb
        open_odb = openOdb
    status = status or (lambda **_: None)
    path = os.path.abspath(request["odb_path"])
    expected = request["expected"]
    if expected["odb_basename"] and os.path.basename(path).casefold() != expected["odb_basename"].casefold():
        raise ValueError("EPS ODB basename does not match the selected ODB")
    started = time.perf_counter()
    status(stage="odb.open", message="Opening ODB read-only", current=0, total=1)
    odb = open_odb(path=path, readOnly=True)
    timings = {"open_odb_seconds": time.perf_counter()-started}
    try:
        at = time.perf_counter()
        frame, index, count, matches = select_frame(odb, expected, request.get("frame_index"))
        if "U" not in frame.fieldOutputs:
            raise ValueError("The EPS-matched ODB frame does not contain displacement field U")
        timings["select_frame_seconds"] = time.perf_counter()-at
        status(stage="odb.displacements", message="Reading verified frame U", current=0, total=1)
        at = time.perf_counter()
        displacements, positions = collections.defaultdict(dict), set()
        for value in frame.fieldOutputs["U"].values:
            position = str(value.position)
            positions.add(position)
            if position != "NODAL":
                raise ValueError("Displacement U must be NODAL, found {}".format(position))
            name, label = value.instance.name, int(value.nodeLabel)
            try:
                raw = value.data
            except Exception:
                raw = value.dataDouble
            data = [float(v) for v in raw]
            if len(data) not in (2, 3) or not all(math.isfinite(v) for v in data):
                raise ValueError("Invalid nodal displacement for {} node {}".format(name, label))
            previous = displacements[name].get(label)
            if previous is not None and previous != data:
                raise ValueError("Conflicting displacement U for {} node {}".format(name, label))
            displacements[name][label] = data
        timings["read_selected_U_seconds"] = time.perf_counter()-at
        at = time.perf_counter()
        instances, types = [], collections.Counter()
        names = sorted(odb.rootAssembly.instances.keys())
        for number, name in enumerate(names):
            status(stage="odb.mesh", message="Reading original model nodes and elements", current=number, total=len(names))
            instance = odb.rootAssembly.instances[name]
            nodes = sorted([[int(node.label)]+[float(v) for v in node.coordinates] for node in instance.nodes])
            if len({node[0] for node in nodes}) != len(nodes):
                raise ValueError("Duplicate node labels in instance {}".format(name))
            if any(not math.isfinite(v) for node in nodes for v in node[1:]):
                raise ValueError("Non-finite original node coordinates")
            by_label = displacements.get(name, {})
            missing = [node[0] for node in nodes if node[0] not in by_label]
            if missing:
                raise ValueError("Instance {} has {} nodes without U; first {}".format(name, len(missing), missing[:10]))
            # Include ALL original element types in the model identity. The
            # geometry backend, not this cache, decides which types it supports.
            elements = sorted([{"label": int(e.label), "type": str(e.type),
                                "connectivity": [int(label) for label in e.connectivity]}
                               for e in instance.elements], key=lambda e: e["label"])
            if len({e["label"] for e in elements}) != len(elements):
                raise ValueError("Duplicate element labels in instance {}".format(name))
            element_types = collections.Counter(e["type"] for e in elements)
            types.update(element_types)
            instances.append({"name": name, "node_count": len(nodes),
                              "element_type_counts": dict(element_types), "exported_element_count": len(elements),
                              "nodes": nodes, "elements": elements,
                              "displacements": [[node[0]]+by_label[node[0]] for node in nodes]})
        timings["read_mesh_seconds"] = time.perf_counter()-at
        result = {"schema": "abaqus-source-mesh-1.8", "instances": instances,
                  "metadata": {"source_odb": path, "source_size_bytes": os.path.getsize(path),
                               "read_only": True, "python": sys.version,
                               "step_name": expected["step_name"], "frame_index": index,
                               "selected_step_frame_count": count, "matching_frame_indices": matches,
                               "selection_criteria": {k:expected[k] for k in ('step_name','increment_number','step_time','time_tolerance')},
                               "frame_value": float(frame.frameValue), "increment_number": int(frame.incrementNumber),
                               "displacement_positions": sorted(positions), "element_type_counts": dict(types),
                               "nodes_columns": ["label", "x", "y", "z"],
                               "displacements_columns": ["label", "U1", "U2", "U3"],
                               "deformation_scale_factor": None,
                               "note": "Original coordinates and verified frame U are separate; no projection/deformation scale inferred.",
                               "api_timings": timings}}
    finally:
        at = time.perf_counter()
        odb.close()
        timings["close_odb_seconds"] = time.perf_counter()-at
    timings["api_total_seconds"] = time.perf_counter()-started
    status(stage="odb.done", message="Verified frame extracted; ODB closed", current=1, total=1)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    with open(args.request, encoding="utf-8") as handle:
        request = json.load(handle)
    output = os.path.abspath(request["output_path"])
    source = os.path.abspath(request["odb_path"])
    if os.path.normcase(output) == os.path.normcase(source) or not output.lower().endswith(".json"):
        raise ValueError("Extractor output must be a separate JSON file")
    status_path = request["status_path"]
    def status(**values):
        write_progress(status_path, values)
    try:
        result = extract(request, status=status)
        atomic_json(output, result)
        status(stage="odb.complete", message="Read-only extraction complete", current=1, total=1)
    except Exception as error:
        # Error reporting must never replace the actual extraction failure.
        failure = dict(stage="odb.error", message=str(error), error_type=type(error).__name__)
        try:
            # Separate from polled progress, including when the launcher loses stderr.
            atomic_json(os.path.join(os.path.dirname(output), 'extraction-error.json'), failure)
        except OSError:
            pass
        try:
            status(**failure)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    main()
