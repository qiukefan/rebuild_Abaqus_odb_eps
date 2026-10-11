"""v0.2.2: register Abaqus C3D20 surface topology to its original EPS.

Read-only inputs. A rejected fit never supplies accepted=True. Matching uses
four-valent corner neighbourhoods, not nearest-neighbour point-cloud guesses.
The saved/live Abaqus perspective camera must belong to the source export.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_deps"))
import numpy as np
from scipy.spatial import cKDTree
from eps_io import read_eps
from deformation import read_eps_deformation

FACE_INDICES = (
    (0, 1, 2, 3, 8, 9, 10, 11), (4, 5, 6, 7, 12, 13, 14, 15),
    (0, 1, 5, 4, 8, 17, 12, 16), (1, 2, 6, 5, 9, 18, 13, 17),
    (2, 3, 7, 6, 10, 19, 14, 18), (3, 0, 4, 7, 11, 16, 15, 19),
)


def parse_eps(path):
    raw = Path(path).read_bytes()
    if raw[:4] == b"\xc5\xd0\xd3\xc6":
        offset, length = struct.unpack("<II", raw[4:12])
        ps = raw[offset:offset + length].decode("latin1")
    else:
        ps = raw.decode("latin1")
    scales = re.findall(r"^([\d.]+) ([\d.]+) scale\s*$", ps, re.M)
    if len(scales) != 1:
        raise ValueError("Only the audited single-scale Abaqus vector EPS is supported")
    sx, sy = map(float, scales[0])
    deformation = read_eps_deformation(path)
    # Use the lossless editor's authoritative IDs, including inline `ls` paints
    # and zero-length fragments emitted by Abaqus' painter sorting algorithm.
    doc = read_eps(path)
    records = []
    for record in doc.black_strokes:
        records.append(dict(vertices=record.points_pt or [p for subpath in record.subpaths_pt for p in subpath],
            subpaths=record.subpaths_pt, closedSubpaths=record.closed_subpaths,
            closed=any(record.closed_subpaths), color=list(record.color_rgb),
            width=record.width_pt, stroked=True, blackStrokeIndex=record.id,
            epsObjectId="sourceBlackStroke-" + str(record.id),
            psSplitlinesLine=record.line_end, protected=record.protected,
            protectionReason=record.protection_reason))
    return records, deformation, hashlib.sha256(raw).hexdigest()


def exterior_mesh(instance):
    coords = {int(n[0]): np.asarray(n[1:], dtype=float) for n in instance["nodes"]}
    displacements = {int(n[0]): np.asarray(n[1:], dtype=float)
                     for n in instance["displacements"]}
    faces = defaultdict(list)
    for element in instance["elements"]:
        if element["type"] not in ("C3D20", "C3D20R", "C3D20H", "C3D20RH"):
            raise ValueError("Only C3D20-family elements are supported")
        conn = element["connectivity"]
        for indices in FACE_INDICES:
            labels = tuple(conn[i] for i in indices)
            faces[tuple(sorted(labels[:4]))].append(labels)
    surface = [value[0] for value in faces.values() if len(value) == 1]
    edges = set()
    for face in surface:
        for k in range(4):
            edges.add((*sorted((face[k], face[(k + 1) % 4])), face[k + 4]))
    graph = defaultdict(set)
    for a, b, mid in edges:
        graph[a].add(mid)
        graph[b].add(mid)
    return coords, displacements, surface, sorted(edges), graph


def neighbourhood_descriptor(center, neighbours):
    vectors = np.asarray(neighbours) - center
    lengths = np.linalg.norm(vectors, axis=1)
    angles = np.arctan2(vectors[:, 1], vectors[:, 0])
    order = np.argsort(angles)
    return np.r_[angles[order], np.log(lengths[order] / np.exp(np.mean(np.log(lengths))))]


def camera_basis(camera):
    position = np.asarray(camera["cameraPosition"], dtype=float)
    forward = np.asarray(camera["cameraTarget"], dtype=float) - position
    distance = float(np.linalg.norm(forward))
    forward /= distance
    right = np.cross(forward, camera["cameraUpVector"])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    return position, forward, right, up, distance


def project_world(world, fit):
    """Return EPS points and -1/camera-Z depth (smaller means nearer)."""
    v = np.asarray(world) - fit["cameraPosition"]
    z = v @ np.asarray(fit["forward"])
    uv = v @ np.asarray([fit["right"], fit["up"]]).T
    uv *= fit["cameraDistance"] / z[..., None]
    return uv @ np.asarray(fit["affine2D"]).T + fit["translation"], -1. / z


def fit_projection(eps_path, mesh_path, camera_path):
    records, deformation, eps_hash = parse_eps(eps_path)
    scale = np.asarray(deformation['factors'], dtype=float)
    mesh = json.loads(Path(mesh_path).read_text(encoding="utf-8"))
    if len(mesh["instances"]) != 1:
        raise ValueError("This version requires a single mesh instance")
    instance = mesh["instances"][0]
    coords, displacement, faces, edges, graph = exterior_mesh(instance)
    camera = json.loads(Path(camera_path).read_text(encoding="utf-8"))
    camera = camera.get("savedView", camera)
    if camera.get("projection", "PERSPECTIVE") != "PERSPECTIVE":
        raise ValueError("Only a verified perspective source camera is supported")
    pos, forward, right, up, distance = camera_basis(camera)
    labels = sorted(coords)
    label_index = {label: i for i, label in enumerate(labels)}
    world = np.array([coords[k] + scale * displacement[k] for k in labels])
    v = world - pos
    camera_z = v @ forward
    if np.any(camera_z <= 0):
        raise ValueError("The source mesh is not wholly in front of the camera")
    projected = (v @ np.array([right, up]).T) * distance / camera_z[:, None]
    model_points = {k: projected[label_index[k]] for k in labels}
    source_lines = [r for r in records if "blackStrokeIndex" in r
                    and len(r["vertices"]) == 2 and not r["closed"]]
    source_graph = defaultdict(set)
    for record in source_lines:
        a, b = [tuple(np.round(p, 6)) for p in record["vertices"]]
        if np.linalg.norm(np.asarray(a) - b) <= .01:
            continue
        source_graph[a].add(b)
        source_graph[b].add(a)
    model_corners = [k for k in graph if len(graph[k]) == 4]
    eps_corners = [k for k in source_graph if len(source_graph[k]) == 4]
    if min(len(model_corners), len(eps_corners)) < 30:
        raise ValueError("Not enough four-valent corners for independent registration")
    model_features = np.array([neighbourhood_descriptor(model_points[k],
        [model_points[n] for n in graph[k]]) for k in model_corners])
    eps_features = np.array([neighbourhood_descriptor(k, list(source_graph[k]))
                             for k in eps_corners])
    descriptor_error, nearest = cKDTree(model_features).query(eps_features)
    matched_labels = np.array([model_corners[i] for i in nearest])
    source = np.array([model_points[k] for k in matched_labels])
    target = np.array(eps_corners)
    design = np.c_[source, np.ones(len(source))]
    keep = descriptor_error < .0005
    if sum(keep) < 30:
        raise ValueError("The camera/topology does not match the source EPS")
    for _ in range(6):
        if sum(keep) < 30:
            raise ValueError("Too few geometrically consistent source correspondences")
        matrix = np.linalg.lstsq(design[keep], target[keep], rcond=None)[0]
        error = np.linalg.norm(design @ matrix - target, axis=1)
        keep = error < .05
    accepted_error = error[keep]
    rms = float(np.sqrt(np.mean(accepted_error ** 2)))
    max_error = float(np.max(accepted_error))
    if rms > .003 or max_error > .01 or len(set(matched_labels[keep])) < 30:
        raise ValueError(f"Rejected registration: RMS={rms:.6f}pt, max={max_error:.6f}pt")
    if np.linalg.matrix_rank(design[keep]) < 3:
        raise ValueError("Source correspondences do not span both image axes")
    # A pixel aspect correction is permitted; rotation/shear must stay tiny.
    screen_matrix = matrix[:2].T
    if min(np.diag(screen_matrix)) <= 0 or max(abs(screen_matrix[0, 1]),
            abs(screen_matrix[1, 0])) > .002 * np.mean(np.diag(screen_matrix)):
        raise ValueError("Registration requires an unexplained screen shear/rotation")
    fit = dict(schema="eps-odb-perspective-fit-0.2.2", version="0.2.2", accepted=True,
        projection="perspective", deformation=deformation,
        cameraPosition=pos.tolist(), right=right.tolist(), up=up.tolist(),
        forward=forward.tolist(), cameraDistance=distance,
        affine2D=screen_matrix.tolist(), translation=matrix[2].tolist(),
        sourceEPS=str(Path(eps_path).resolve()), sourceMesh=str(Path(mesh_path).resolve()),
        sourceCamera=str(Path(camera_path).resolve()), sourceEPSsha256=eps_hash,
        validation=dict(matchedNodes=int(sum(keep)), nodeRmsPt=rms,
            nodeMaxPt=max_error, strictRmsThresholdPt=.003,
            correspondenceBounds=[target[keep].min(0).tolist(), target[keep].max(0).tolist()],
            exteriorFaces=len(faces), exteriorEdges=len(edges)),
        formula="v=world-cameraPosition; cameraZ=dot(v,forward); normalizedXY="
            "[dot(v,right),dot(v,up)]*cameraDistance/cameraZ; "
            "screenXY=affine2D @ normalizedXY+translation; depth=-1/cameraZ (smaller=closer)")
    if deformation['mode'] == 'UNIFORM':
        fit['deformationScale'] = float(scale[0])  # Compatibility with older uniform-fit readers.
    screen, _ = project_world(world, fit)
    halves = [(a, m, ei, 0) for ei, (a, b, m) in enumerate(edges)]
    halves += [(m, b, ei, 1) for ei, (a, b, m) in enumerate(edges)]
    starts = np.array([screen[label_index[a]] for a, b, ei, hi in halves])
    ends = np.array([screen[label_index[b]] for a, b, ei, hi in halves])
    delta = ends - starts
    length2 = np.sum(delta * delta, axis=1)
    samples = starts[:, None, :] + np.linspace(0, 1, 9)[None, :, None] * delta[:, None, :]
    tree = cKDTree(samples.reshape(-1, 2))
    matches, unmatched = [], []
    def match_segment(segment):
        segment = np.asarray(segment)
        _, nearest_samples = tree.query(segment.mean(0), k=64)
        candidates = np.unique(nearest_samples // 9)
        param = np.sum((segment[None, :, :] - starts[candidates, None, :]) *
                       delta[candidates, None, :], axis=2) / length2[candidates, None]
        prediction = starts[candidates, None, :] + np.clip(param, 0, 1)[:, :, None] * delta[candidates, None, :]
        residual = np.max(np.linalg.norm(prediction - segment, axis=2), axis=1)
        best = int(np.argmin(residual))
        half_index = int(candidates[best])
        _, _, edge_index, half = halves[half_index]
        return dict(vertices=segment.tolist(), maxResidualPt=float(residual[best]),
            edgeLabels=list(edges[edge_index]), half=half,
            sourceHalfParameters=param[best].tolist())

    for record in records:
        subsegments = []
        for points, closed in zip(record["subpaths"], record["closedSubpaths"]):
            points = list(points)
            if closed and points and points[-1] != points[0]:
                points.append(points[0])
            subsegments.extend(match_segment([a, b]) for a, b in zip(points, points[1:]))
        info = dict(blackStrokeIndex=record["blackStrokeIndex"],
            epsObjectId=record["epsObjectId"], psSplitlinesLine=record["psSplitlinesLine"],
            vertices=record["vertices"], segments=subsegments,
            maxResidualPt=max((s["maxResidualPt"] for s in subsegments), default=1e30))
        if len(subsegments) == 1:
            info.update({k: subsegments[0][k] for k in ("edgeLabels", "half", "sourceHalfParameters")})
        if subsegments and info["maxResidualPt"] <= .003:
            matches.append(info)
        else:
            unmatched.append(info)
    # Other black shapes are retained, e.g. the closed legend frame.
    fit["strokeMatches"] = matches
    fit["unmatchedBlackLines"] = unmatched
    unmatched_ids = {m["blackStrokeIndex"] for m in unmatched}
    fit["retainedOtherBlackPaths"] = [dict(blackStrokeIndex=r["blackStrokeIndex"],
        epsObjectId=r["epsObjectId"], vertices=r["vertices"])
        for r in records if r["blackStrokeIndex"] in unmatched_ids and r not in source_lines]
    fit["validation"].update(matchedBlackMeshStrokes=len(matches),
        unmatchedBlackLines=len(unmatched),
        maxMatchedStrokeResidualPt=max(m["maxResidualPt"] for m in matches))
    if len(matches) < 100:
        raise ValueError("Not enough independent mesh strokes verify the registration")
    return fit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eps", required=True)
    parser.add_argument("--mesh", required=True)
    parser.add_argument("--camera", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.suffix.lower() != '.json' or output in [Path(p).resolve() for p in (args.eps, args.mesh, args.camera)]:
        raise ValueError('Calibration output must be a separate JSON file')
    if output.exists():
        raise FileExistsError('Choose a new calibration output filename')
    try:
        result = fit_projection(args.eps, args.mesh, args.camera)
    except (ValueError, KeyError) as exc:
        result = dict(schema="eps-odb-perspective-fit-0.2.2", version="0.2.2",
                      accepted=False, error=str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("strokeMatches", "unmatchedBlackLines", "retainedOtherBlackPaths")}, indent=2))
    if not result["accepted"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
