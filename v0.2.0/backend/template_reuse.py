"""v0.2.0: conservative 2-D topology reuse; no target ODB/U/depth is read.

The template carries labelled reference projections, not a reusable visibility
mask. Output coordinates come from the CURRENT EPS. Visibility is accepted only
for a closed oriented surface whose CURRENT front-facing projected faces have
no interior overlap and whose footprint matches the current coloured artwork.
Ambiguous correspondences, clipping, folds, overlap, or coverage reject reuse.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / '_deps'))
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree
import shapely

from eps_io import read_eps, write_rebuilt_eps
from fit_projection import project_world
from surface_visibility import exterior_topology

VERSION = '0.2.0'
POINT_TOLERANCE = .0012
ANGLE_TOLERANCE = math.radians(.75)
AREA_TOLERANCE = .00001


def _reject(message):
    raise ValueError('模板免 ODB 校验未通过：' + message + '。请使用当前工况、当前帧的 ODB 重建；未修改原图。')


def _check_cancel(cancel):
    if cancel is not None and cancel():
        raise ValueError('用户已取消模板重建；未修改原图。')


def _progress(callback, percent, message, cancel=None):
    _check_cancel(cancel)
    if callback:
        callback({'progress': percent, 'message': message})


def _atomic_json(path, data):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _viewport_rect(document):
    text = document.ps[document.viewport_start:document.overlay_offset]
    match = re.search(r'(?m)^\s*([-\d.]+)\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rectfill\s*$', text)
    if not match:
        _reject('找不到可验证的原始 Abaqus 画框')
    x, y, w, h = map(float, match.groups())
    matrix = document.overlay_matrix
    if abs(matrix[1]) + abs(matrix[2]) > 1e-12 or matrix[0] <= 0 or matrix[3] <= 0:
        _reject('画框存在旋转或不支持的坐标变换')
    return np.array([x*matrix[0]+matrix[4], y*matrix[3]+matrix[5],
                     (x+w)*matrix[0]+matrix[4], (y+h)*matrix[3]+matrix[5]])


def create_template(eps_path, mesh_path, fit_path, output_path):
    """Atomically replace a template after a successful ODB registration."""
    document = read_eps(eps_path)
    fit = json.loads(Path(fit_path).read_text(encoding='utf-8-sig'))
    mesh = json.loads(Path(mesh_path).read_text(encoding='utf-8-sig'))
    if not fit.get('accepted') or fit.get('sourceEPSsha256') != document.source_sha256:
        _reject('源 EPS 与已验证的配准记录不一致')
    validation = fit.get('validation', {})
    if validation.get('nodeRmsPt', 1e30) > .003 or validation.get('matchedNodes', 0) < 100:
        _reject('源配准精度不足，不能建立模板')
    if len(mesh['instances']) != 1:
        _reject('模板只支持一个封闭 C3D20 实例')
    instance = mesh['instances'][0]
    faces, edges = exterior_topology(instance)
    labels = sorted({n for face in faces for n in face})
    xyz = {r[0]: np.asarray(r[1:]) for r in instance['nodes']}
    disp = {r[0]: np.asarray(r[1:]) for r in instance['displacements']}
    world = np.array([xyz[n]+float(fit['deformationScale'])*disp[n] for n in labels])
    xy, _ = project_world(world, fit)
    matched_ids = sorted(m['blackStrokeIndex'] for m in fit.get('strokeMatches', []))
    if matched_ids != sorted(r.index for r in document.removable_strokes):
        _reject('源图中还有未验证的网格黑线')
    target = Path(output_path).resolve()
    if target in {Path(p).resolve() for p in (eps_path, mesh_path, fit_path)}:
        _reject('模板路径与输入文件重合')
    payload = dict(schema='surface-mesh-template-0.2.0', version=VERSION,
        referenceEPS=str(Path(eps_path).resolve()), referenceEPSsha256=document.source_sha256,
        referenceMeshSha256=hashlib.sha256(Path(mesh_path).read_bytes()).hexdigest(),
        referenceNodeXY=[[int(n), float(p[0]), float(p[1])] for n, p in zip(labels, xy)],
        faces=[list(map(int, f)) for f in faces],
        edges=[dict(nodes=list(map(int, e['nodes'])), faces=list(map(int, e['faces']))) for e in edges],
        viewportRectPt=_viewport_rect(document).tolist(), referenceValidation=validation,
        restrictions=['仅同模型、同视角、小投影变化', '新坐标仅取当前 EPS',
                      '当前正面投影不得互相重叠', '当前彩色面覆盖必须一致',
                      '任何不确定项回退当前帧 ODB'],
        containsTargetODB=False, containsDisplacements=False, containsReusableVisibilityMask=False)
    _atomic_json(target, payload)
    return {'version': VERSION, 'templatePath': str(target), 'templateReady': True,
            'nodes': len(labels), 'faces': len(faces)}


def _eps_geometry(document):
    segments, graph, points = [], defaultdict(set), set()
    for record in document.removable_strokes:
        if any(record.closed_subpaths):
            _reject('模型区域存在闭合黑色路径，无法确认其网格含义')
        for path_index, path in enumerate(record.subpaths_pt):
            for segment_index, (a, b) in enumerate(zip(path, path[1:])):
                a, b = tuple(a), tuple(b)
                points.update((a, b))
                segments.append(dict(stroke=record.index, path=path_index,
                                     segment=segment_index, points=np.array([a, b])))
                if a != b:
                    graph[a].add(b)
                    graph[b].add(a)
    if len(points) < 100:
        _reject('当前图中没有足够的原始网格节点')
    return segments, graph, np.array(sorted(points))


def _model_graph(edges):
    graph = defaultdict(set)
    half_edges = []
    for edge_index, edge in enumerate(edges):
        for half, (a, b) in enumerate(zip(edge['nodes'], edge['nodes'][1:])):
            graph[a].add(b)
            graph[b].add(a)
            half_edges.append((a, b, edge_index, half))
    return graph, half_edges


def _on_boundary(point, rect, tolerance=POINT_TOLERANCE):
    return {i for i, value in enumerate((point[0]-rect[0], point[1]-rect[1],
                                      point[0]-rect[2], point[1]-rect[3]))
            if abs(value) <= tolerance}


def _recover_nodes(reference, model_graph, current_points, current_graph, rect, cancel=None):
    """Seed locally, then use topology/current incident lines to resolve close nodes."""
    # Clipping intersections are never relabelled as actual model nodes.
    pts = np.array([p for p in current_points if not _on_boundary(p, rect)])
    if len(pts) < 100:
        _reject('当前图仅保留裁剪点，无法识别真实节点')
    labels = sorted(reference)
    ref = np.array([reference[n] for n in labels])
    tree = cKDTree(pts)
    distances, indices = tree.query(ref, k=2)
    reverse = cKDTree(ref).query(pts)[1]
    good = ((distances[:, 0] < .8) & (distances[:, 0] < .3*distances[:, 1]) &
            (reverse[indices[:, 0]] == np.arange(len(labels))))
    known = {n: pts[i] for n, i, keep in zip(labels, indices[:, 0], good) if keep}
    if len(known) < max(100, .2*len(pts)):
        _reject('视角或变形变化过大，可靠拓扑种子不足')
    for _ in range(12):
        _check_cancel(cancel)
        updates = {}
        for n in labels:
            neighbours = [k for k in model_graph[n] if k in known]
            if not neighbours:
                continue
            delta = np.mean([known[k]-reference[k] for k in neighbours], axis=0)
            predicted = reference[n]+delta
            ds, ids = tree.query(predicted, k=2)
            if ds[0] < .08 and ds[0] < .2*ds[1]:
                updates[n] = pts[ids[0]]
        changed = sum(n not in known or np.linalg.norm(p-known[n]) > 1e-8 for n, p in updates.items())
        known.update(updates)
        if not changed:
            break
    for _ in range(4):
        _check_cancel(cancel)
        updates = {}
        for n in labels:
            neighbours = [k for k in model_graph[n] if k in known]
            if len(neighbours) < 2:
                continue
            predicted = reference[n]+np.mean([known[k]-reference[k] for k in neighbours], axis=0)
            ds, ids = tree.query(predicted, k=8)
            choices = []
            for distance, index in zip(ds, ids):
                if distance > .8:
                    continue
                q = pts[index]
                votes = 0
                for nb in neighbours:
                    anchor = known[nb]
                    delta = q-anchor
                    errors = []
                    for other in current_graph.get(tuple(anchor), ()):
                        v = np.array(other)-anchor
                        if np.dot(v, delta) > 0:
                            errors.append(abs(v[0]*delta[1]-v[1]*delta[0])/np.linalg.norm(v))
                    if errors and min(errors) < POINT_TOLERANCE:
                        votes += 1
                choices.append((votes, float(distance), q))
            choices.sort(key=lambda item: (-item[0], item[1]))
            if choices:
                best = choices[0]
                if best[0] >= 2 and (len(choices) < 2 or choices[1][0] < best[0] or best[1] < .2*choices[1][1]):
                    updates[n] = best[2]
        changed = sum(n not in known or np.linalg.norm(p-known[n]) > 1e-8 for n, p in updates.items())
        known.update(updates)
        if not changed:
            break
    reverse_mapping = defaultdict(list)
    for n, point in known.items():
        reverse_mapping[tuple(point)].append(n)
    if any(len(v) != 1 for v in reverse_mapping.values()):
        _reject('近重合节点仍有多个可能标签')
    if max(np.linalg.norm(p-reference[n]) for n, p in known.items()) > 1.5:
        _reject('当前节点相对模板的投影变化超过保守范围')
    return known


def _match_current_edges(known, reference, half_edges, segments, rect, cancel=None):
    complete = [(a, b, ei, hi) for a, b, ei, hi in half_edges if a in known and b in known]
    starts = np.array([known[a] for a, b, ei, hi in complete])
    delta = np.array([known[b] for a, b, ei, hi in complete])-starts
    length2 = np.einsum('ij,ij->i', delta, delta)
    if np.any(length2 < 1e-12):
        _reject('恢复的网格存在退化半边')
    samples = starts[:, None, :]+np.linspace(0, 1, 9)[None, :, None]*delta[:, None, :]
    tree = cKDTree(samples.reshape(-1, 2))
    reverse = {tuple(p): n for n, p in known.items()}
    incident = defaultdict(list)
    for a, b, ei, hi in half_edges:
        incident[a].append((b, ei, hi))
        incident[b].append((a, ei, hi))
    clipped = {}
    matched = []
    support = defaultdict(int)
    for index, source in enumerate(segments):
        if index % 256 == 0:
            _check_cancel(cancel)
        segment = source['points']
        _, ids = tree.query(segment.mean(0), k=min(48, len(samples)*9))
        ids = np.unique(np.atleast_1d(ids)//9)
        parameters = np.sum((segment[None, :, :]-starts[ids, None, :])*delta[ids, None, :], axis=2)/length2[ids, None]
        p = starts[ids, None, :]+np.clip(parameters, 0, 1)[:, :, None]*delta[ids, None, :]
        residual = np.max(np.linalg.norm(p-segment, axis=2), axis=1)
        order = np.argsort(residual)
        selected = None
        if residual[order[0]] <= POINT_TOLERANCE:
            # Zero-length paints at a shared vertex may belong to several edges;
            # they carry no new geometry and do not control node correspondence.
            if np.linalg.norm(segment[1]-segment[0]) > .01:
                options = [complete[ids[i]] for i in order if residual[i] <= POINT_TOLERANCE]
                different = {(a, b) for a, b, ei, hi in options}
                if len(different) > 1:
                    _reject('当前黑线同时落在多条不同网格半边上，无法唯一消歧')
            selected = complete[ids[order[0]]]
            error = float(residual[order[0]])
        else:
            # A clipped half-edge is accepted only when one true node is known,
            # its other end is on the current viewport, and topology gives one
            # outward direction. No missing coordinate is extrapolated.
            choices = []
            for a, b in (segment, segment[::-1]):
                node = reverse.get(tuple(a))
                if node is None or not _on_boundary(b, rect):
                    continue
                vector = b-a
                if np.linalg.norm(vector) < 1e-6:
                    continue
                for other, edge, half in incident[node]:
                    if other in known:
                        continue
                    original = reference[other]-reference[node]
                    angle = math.atan2(abs(original[0]*vector[1]-original[1]*vector[0]), float(np.dot(original, vector)))
                    if angle < ANGLE_TOLERANCE and np.linalg.norm(vector) < 1.2*np.linalg.norm(original):
                        choices.append((node, other, edge, half, a, b, angle))
            if len(choices) != 1:
                _reject(f'第 {source["stroke"]} 条网格路径缺少当前节点或具有不明确的裁剪方向')
            a, b, ei, hi, pa, pb, angle = choices[0]
            key = tuple(sorted((a, b)))
            entry = (a, b, np.array([pa, pb]))
            if key in clipped and np.linalg.norm(clipped[key][2]-entry[2]) > POINT_TOLERANCE:
                _reject('同一裁剪半边出现不一致的当前几何')
            clipped[key] = entry
            selected, error = (a, b, ei, hi), 0.
        a, b, ei, hi = selected
        support[tuple(sorted((a, b)))] += 1
        matched.append(dict(stroke=int(source['stroke']), edge=int(ei), half=int(hi),
                            maxResidualPt=error))
    # An inferred node pair without any current black-line evidence is not
    # allowed to invent a missing complete edge.
    for a, b, ei, hi in complete:
        if tuple(sorted((a, b))) not in support:
            _reject(f'真实节点 {a}–{b} 之间缺少当前黑线证据')
    paths = {tuple(sorted((a, b))): (a, b, np.array([known[a], known[b]])) for a, b, ei, hi in complete}
    paths.update(clipped)
    return paths, matched, dict(completeHalfEdges=len(complete), clippedHalfEdges=len(clipped),
        maxCurrentLineResidualPt=max(m['maxResidualPt'] for m in matched))


def _join_clip_gap(end, start, rect):
    if np.linalg.norm(end-start) <= POINT_TOLERANCE:
        return []
    ends, starts = _on_boundary(end, rect), _on_boundary(start, rect)
    if not ends or not starts:
        _reject('面边界在画框内部断开，不能推测补齐')
    if ends & starts:
        return []
    corners = []
    for xside in (0, 2):
        for yside in (1, 3):
            if xside in ends and yside in starts or xside in starts and yside in ends:
                corners.append(np.array([rect[xside], rect[yside]]))
    if len(corners) != 1:
        _reject('表面跨越相对的画框边界，裁剪归属不唯一')
    return corners


def _current_faces(faces, paths, rect):
    fronts, front_ids = [], []
    reconstructed = 0
    for face_id, face in enumerate(faces):
        ordered = [n for i in range(4) for n in (face[i], face[4+i])]
        points = []
        for a, b in zip(ordered, ordered[1:]+ordered[:1]):
            path = paths.get(tuple(sorted((a, b))))
            if path is None:
                continue
            curve = path[2] if path[0] == a else path[2][::-1]
            if points:
                points.extend(_join_clip_gap(points[-1], curve[0], rect))
            points.extend(curve)
        if len(points) < 3:
            continue
        points.extend(_join_clip_gap(points[-1], points[0], rect))
        # Remove duplicate vertices while preserving oriented connectivity.
        cleaned = []
        for point in points:
            if not cleaned or np.linalg.norm(point-cleaned[-1]) > 1e-9:
                cleaned.append(point)
        if len(cleaned) < 3:
            continue
        signed_area = sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(cleaned, cleaned[1:]+cleaned[:1]))/2
        if abs(signed_area) <= AREA_TOLERANCE:
            _reject('当前表面接近投影退化状态，需要三维深度验证')
        polygon = Polygon(cleaned)
        if not polygon.is_valid:
            _reject('当前表面边界发生自交或折叠')
        reconstructed += 1
        if signed_area > 0:
            fronts.append(polygon)
            front_ids.append(face_id)
    if len(fronts) < 10:
        _reject('能从当前图完整恢复的正面单元不足')
    tree = STRtree(fronts)
    maximum_overlap = 0.
    for i, polygon in enumerate(fronts):
        for j in tree.query(polygon):
            if j <= i:
                continue
            overlap = polygon.intersection(fronts[j]).area
            maximum_overlap = max(maximum_overlap, overlap)
            if overlap > AREA_TOLERANCE:
                _reject(f'正面单元在当前视图发生遮挡重叠（{overlap:.6g} pt²），需要当前三维深度')
    return fronts, set(front_ids), dict(reconstructedProjectedFaces=reconstructed,
        currentFrontFaces=len(fronts), maxFrontFaceOverlapAreaPt2=maximum_overlap)


def _colored_triangles(document):
    text = document.ps[document.viewport_start:document.overlay_offset]
    annotation = text.find('% the gouraudtriangle')
    if annotation >= 0:
        text = text[:annotation]
    sx, _, _, sy, tx, ty = document.overlay_matrix
    color, points, triangles = [0., 0., 0.], [], []
    for line in text.splitlines():
        line = line.strip()
        match = re.fullmatch(r'([\d.]+) ([\d.]+) ([\d.]+) setrgbcolor', line)
        if match:
            color = list(map(float, match.groups()))
        match = re.fullmatch(r'(-?[\d.]+) (-?[\d.]+) ([ml])', line)
        if match:
            if match[3] == 'm':
                points = []
            points.append((float(match[1])*sx+tx, float(match[2])*sy+ty))
        if line == 'fill':
            if len(points) == 3 and color not in ([0., 0., 0.], [1., 1., 1.]):
                poly = Polygon(points)
                if poly.area > 1e-9:
                    triangles.append(poly)
            points = []
    if not triangles:
        _reject('当前 EPS 没有可验证的平面填色三角形')
    return triangles


def _coverage(fronts, document):
    surface = unary_union(fronts)
    triangles = _colored_triangles(document)
    # Disconnected annotation artwork is outside the already audited main mesh
    # rectangle; it is never used to prove surface coverage.
    bounds = box(*document.mesh_bounds_pt)
    coloured = unary_union([t.intersection(bounds) for t in triangles if t.intersects(bounds)])
    unsupported = surface.difference(coloured.buffer(POINT_TOLERANCE)).area
    unexplained = coloured.difference(surface.buffer(POINT_TOLERANCE)).area
    if unsupported > AREA_TOLERANCE or unexplained > AREA_TOLERANCE:
        _reject(f'当前填色面与恢复表面覆盖不一致（缺失 {unsupported:.6g}、额外 {unexplained:.6g} pt²）')
    return dict(currentColoredTriangles=len(triangles), frontAreaPt2=surface.area,
        currentColoredAreaPt2=coloured.area, unsupportedAreaPt2=unsupported,
        unexplainedColoredAreaPt2=unexplained, coverageBoundaryTolerancePt=POINT_TOLERANCE)


def _new_output_paths(output_dir, source):
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    suffix_label = '_mesh_v0.2.0_template'
    available = min(80, 228-len(str(directory))-len(suffix_label))
    if available < 12:
        _reject('输出目录路径太长，请选择较短的输出目录')
    stem = Path(source).stem[:available] + suffix_label
    suffix = ''
    number = 1
    while any((directory / (stem+suffix+ext)).exists() for ext in ('.eps', '.report.json', '.plan.json')):
        number += 1
        suffix = '_' + str(number)
    base = directory / (stem+suffix)
    return Path(str(base)+'.eps'), Path(str(base)+'.report.json'), Path(str(base)+'.plan.json')


def rebuild_from_template(eps_path, template_path, output_dir, progress=None, cancel=None):
    started = time.perf_counter()
    _progress(progress, 1, '读取 v0.2.0 模板及当前 EPS', cancel)
    template = json.loads(Path(template_path).read_text(encoding='utf-8-sig'))
    if template.get('schema') != 'surface-mesh-template-0.2.0':
        _reject('模板格式或版本不兼容')
    from odb_cache import read_eps_metadata
    metadata = read_eps_metadata(eps_path)
    document = read_eps(eps_path)
    rect = _viewport_rect(document)
    if np.max(np.abs(rect-np.asarray(template['viewportRectPt']))) > POINT_TOLERANCE:
        _reject('画框尺寸发生变化，不能确认同一视角')
    reference = {int(r[0]): np.array(r[1:]) for r in template['referenceNodeXY']}
    model_graph, half_edges = _model_graph(template['edges'])
    segments, current_graph, points = _eps_geometry(document)
    _progress(progress, 12, '从当前 EPS 识别真实节点并校验拓扑', cancel)
    known = _recover_nodes(reference, model_graph, points, current_graph, rect, cancel)
    _progress(progress, 38, '逐条校验当前网格边和画框裁剪段', cancel)
    paths, matches, line_validation = _match_current_edges(known, reference, half_edges, segments, rect, cancel)
    _progress(progress, 60, '校验当前正面单元朝向与投影重叠', cancel)
    fronts, front_ids, face_validation = _current_faces(template['faces'], paths, rect)
    _progress(progress, 78, '比较当前彩色面与当前网格表面的精确覆盖', cancel)
    coverage = _coverage(fronts, document)
    overlay = []
    for a, b, edge_index, half in half_edges:
        if not front_ids.intersection(template['edges'][edge_index]['faces']):
            continue
        path = paths.get(tuple(sorted((a, b))))
        if path is not None:
            overlay.append(dict(points_pt=path[2].tolist(), edge=edge_index, half=half))
    if not overlay:
        _reject('没有通过可见性证明的当前网格边')
    ids = sorted({m['stroke'] for m in matches})
    if ids != sorted(r.index for r in document.removable_strokes):
        _reject('有原黑线尚未通过当前拓扑校验')
    output, report_path, plan_path = _new_output_paths(output_dir, eps_path)
    _progress(progress, 92, '生成独立副本中的表面网格', cancel)
    plan = dict(version=VERSION, method='current-EPS topology + oriented nonoverlapping front faces',
        sourceEPS=str(Path(eps_path).resolve()), sourceEPSsha256=document.source_sha256,
        templatePath=str(Path(template_path).resolve()), removeIds=ids, overlayPaths=overlay,
        currentNodeXY=[[int(n), float(p[0]), float(p[1])] for n, p in sorted(known.items())],
        currentFrontFaceIds=sorted(front_ids), matches=matches)
    report = dict(version=VERSION, mode='template-current-eps', accepted=True,
        sourceEPS=plan['sourceEPS'], sourceEPSsha256=document.source_sha256,
        epsMetadata=metadata,
        templatePath=plan['templatePath'], referenceEPSsha256=template['referenceEPSsha256'],
        outputEPS=str(output), reportPath=str(report_path), planPath=str(plan_path),
        targetODBRead=False, targetMeshJSONRead=False, oldDisplacementsReused=False,
        oldVisibilityMaskReused=False, outputCoordinatesSource='current EPS only',
        validation=dict(recoveredCurrentNodes=len(known), **line_validation, **face_validation, **coverage),
        replacedOriginalBlackPaths=len(ids), generatedHalfEdges=len(overlay),
        shapelyVersion=shapely.__version__, elapsedSeconds=time.perf_counter()-started)
    _check_cancel(cancel)
    write_rebuilt_eps(document, output, ids, overlay)
    _atomic_json(plan_path, plan)
    _atomic_json(report_path, report)
    _progress(progress, 100, '模板重建完成，当前 EPS 的拓扑和表面覆盖均通过校验')
    return {key: report[key] for key in ('version', 'mode', 'accepted', 'outputEPS',
                                       'reportPath', 'planPath', 'templatePath')}
