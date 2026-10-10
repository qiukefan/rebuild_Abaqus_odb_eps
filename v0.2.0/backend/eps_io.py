"""Lossless paint-token edits for the explicit paths in Abaqus EPS exports.

This is deliberately not a general PostScript interpreter. Unknown path-building
syntax is left untouched. Triangle fill/stroke pairs, text and annotations are
never removable records. Coordinates are viewport EPS points (the explicit
viewport scale is applied, excluding the enclosing page translation).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import statistics
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
NUMBERS = re.compile(NUMBER)
IDENTITY = (1., 0., 0., 1., 0., 0.)
EPS_MAGIC = b"\xc5\xd0\xd3\xc6"


def _point(matrix, point):
    a, b, c, d, e, f = matrix
    x, y = point
    return (a*x+c*y+e, b*x+d*y+f)


def _multiply(a, b):
    return (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5])


def _inverse(m):
    a, b, c, d, e, f = m
    determinant = a*d-b*c
    if abs(determinant) < 1e-15:
        raise ValueError("Singular viewport transformation")
    return (d/determinant, -b/determinant, -c/determinant, a/determinant,
            (c*f-d*e)/determinant, (b*e-a*f)/determinant)


def _uniform_scale(m):
    x, y = math.hypot(m[0], m[1]), math.hypot(m[2], m[3])
    if x <= 0 or abs(x-y) > max(x, y)*1e-9 or abs(m[0]*m[2]+m[1]*m[3]) > x*y*1e-9:
        raise ValueError("Only uniform viewport scales/rotations are supported for stroke width")
    return x


@dataclass
class StrokeRecord:
    index: int
    subpaths_pt: list[list[tuple[float, float]]]
    closed_subpaths: list[bool]
    width_pt: float
    color_rgb: tuple[float, float, float]
    line_start: int
    line_end: int
    path_start: int
    path_end: int
    paint_start: int
    paint_end: int
    paint_operator: str
    source_paint_start: int
    source_paint_end: int
    protected: bool = True
    protection_reason: str = "outside the main colored surface"

    @property
    def points_pt(self):
        """Convenient single-subpath access; compound subpaths remain explicit."""
        return self.subpaths_pt[0] if len(self.subpaths_pt) == 1 else None

    @property
    def id(self):
        return self.index

    def to_dict(self):
        result = asdict(self)
        result["points_pt"] = self.points_pt
        result["id"] = self.index
        return result


@dataclass
class EpsDocument:
    source: Path
    source_sha256: str
    ps: str
    payload_offset: int
    payload_length: int
    viewport_start: int
    overlay_offset: int
    overlay_matrix: tuple[float, ...]
    black_strokes: list[StrokeRecord]
    mesh_bounds_pt: tuple[float, float, float, float] | None
    warnings: list[str] = field(default_factory=list)

    @property
    def removable_strokes(self):
        return [record for record in self.black_strokes if not record.protected]

    @property
    def default_stroke_width_pt(self):
        widths = [record.width_pt for record in self.removable_strokes if record.width_pt > 0]
        return statistics.median(widths) if widths else 0.5670756

    def summary(self):
        return {"source": str(self.source), "source_sha256": self.source_sha256,
                "payload_offset": self.payload_offset, "payload_length": self.payload_length,
                "viewport_start": self.viewport_start, "overlay_offset": self.overlay_offset,
                "overlay_matrix": self.overlay_matrix, "mesh_bounds_pt": self.mesh_bounds_pt,
                "black_strokes": len(self.black_strokes), "removable_strokes": len(self.removable_strokes),
                "protected_strokes": sum(record.protected for record in self.black_strokes),
                "default_stroke_width_pt": self.default_stroke_width_pt, "warnings": self.warnings}


def extract_postscript(raw: bytes) -> tuple[str, int, int]:
    """Return Latin-1 PS and exact byte slice; discard the DOS EPS preview only."""
    if raw.startswith(EPS_MAGIC):
        if len(raw) < 30:
            raise ValueError("Truncated binary EPS header")
        offset, length = struct.unpack_from("<II", raw, 4)
        if offset < 30 or length == 0 or offset+length > len(raw):
            raise ValueError("Invalid binary EPS PostScript offset/length")
    else:
        offset = 3 if raw.startswith(b"\xef\xbb\xbf") else 0
        length = len(raw)-offset
    payload = raw[offset:offset+length]
    if not payload.startswith(b"%!"):
        raise ValueError("No PostScript header at the declared EPS payload offset")
    return payload.decode("latin1"), offset, length


def _viewport(ps: str):
    marker = re.search(r"(?m)^%%BeginDocument:\s*\(Viewport Vector Image\)\s*$", ps)
    scale = re.search(rf"(?m)^\s*({NUMBER})\s+({NUMBER})\s+scale\s*$", ps[marker.end():] if marker else ps)
    if scale:
        start = (marker.end() if marker else 0)+scale.start()
    else:
        # A minimal plain EPS is supported; procedure-definition lines are not executed.
        start = marker.end() if marker else 0
    end_pattern = re.compile(r"(?m)^[ \t]*grestore[ \t]*\r?\n(?:[ \t]*\r?\n)*showpage[ \t]*\r?\n%%EOF[ \t]*\r?\n%%EndDocument")
    endings = list(end_pattern.finditer(ps))
    if endings:
        end = endings[-1].start()
    else:
        shows = list(re.finditer(r"(?m)^[ \t]*showpage[ \t]*(?:%[^\r\n]*)?$", ps))
        if not shows:
            raise ValueError("Cannot locate a safe viewport overlay insertion point")
        end = shows[-1].start()
        previous = ps[:end].rstrip()
        last_line = previous.rsplit("\n", 1)[-1].strip()
        if last_line == "grestore":
            end = previous.rfind("grestore")
    if start >= end:
        raise ValueError("Viewport bounds are invalid")
    return start, end


def _main_surface_bounds(triangles):
    if not triangles:
        return None
    parent = list(range(len(triangles)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    edges = {}
    for i, points in enumerate(triangles):
        for a, b in zip(points, points[1:]+points[:1]):
            key = tuple(sorted((tuple(round(v, 6) for v in a), tuple(round(v, 6) for v in b))))
            if key in edges:
                parent[root(i)] = root(edges[key])
            else:
                edges[key] = i
    groups = {}
    for i, points in enumerate(triangles):
        item = groups.setdefault(root(i), {"area": 0., "points": []})
        a, b, c = points
        item["area"] += abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))/2
        item["points"].extend(points)
    points = max(groups.values(), key=lambda x: x["area"])["points"]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def read_eps(source, *, mesh_bounds_pt=None) -> EpsDocument:
    source = Path(source).resolve()
    raw = source.read_bytes()
    ps, payload_offset, payload_length = extract_postscript(raw)
    start, end = _viewport(ps)
    color, width, matrix = (0., 0., 0.), 1., IDENTITY
    subpaths, closed, path_start, path_line = [], [], None, None
    path_token, next_path_token, filled_tokens = None, 0, set()
    unsafe = False
    records, record_path_tokens, triangles, warnings, stack = [], [], [], [], []
    annotation_start = ps.find("% the gouraudtriangle", start)
    if annotation_start < 0:
        annotation_start = end
    offset = 0
    for number, line in enumerate(ps.splitlines(keepends=True), 1):
        line_offset = offset
        offset += len(line)
        if line_offset < start or line_offset >= end:
            continue
        code = line.split("%", 1)[0].strip()
        if not code:
            continue
        numeric = re.fullmatch(rf"\s*(({NUMBER}\s+)+)([A-Za-z]+)\s*", code)
        values = [float(x) for x in NUMBERS.findall(numeric.group(1))] if numeric else []
        operator = numeric.group(3) if numeric else code
        if values and not all(math.isfinite(v) for v in values):
            raise ValueError(f"Non-finite number at PS line {number}")
        if operator == "setrgbcolor" and len(values) == 3:
            color = tuple(values)
        elif operator == "setgray" and len(values) == 1:
            color = (values[0],)*3
        elif operator == "setcmykcolor" and len(values) == 4:
            color = tuple(1-min(1, channel+values[3]) for channel in values[:3])
        elif operator == "setlinewidth" and len(values) == 1:
            width = values[0]
        elif operator in ("scale", "translate", "rotate"):
            if subpaths:
                unsafe = True
            if operator == "scale" and len(values) == 2:
                transform = (values[0], 0, 0, values[1], 0, 0)
            elif operator == "translate" and len(values) == 2:
                transform = (1, 0, 0, 1, values[0], values[1])
            elif operator == "rotate" and len(values) == 1:
                angle = math.radians(values[0]); co, si = math.cos(angle), math.sin(angle)
                transform = (co, si, -si, co, 0, 0)
            else:
                raise ValueError(f"Unsupported transform at line {number}")
            matrix = _multiply(matrix, transform)
        elif operator == "gsave":
            stack.append(copy.deepcopy((color, width, matrix, subpaths, closed, path_start, path_line, unsafe, path_token)))
        elif operator == "grestore":
            if not stack:
                raise ValueError(f"Unexpected grestore inside viewport at line {number}")
            color, width, matrix, subpaths, closed, path_start, path_line, unsafe, path_token = stack.pop()
        elif operator == "newpath":
            subpaths, closed, path_start, path_line, unsafe = [], [], None, None, False
            path_token = None
        elif operator in ("m", "moveto") and len(values) == 2:
            if path_start is None:
                path_start, path_line = line_offset, number
                path_token, next_path_token = next_path_token, next_path_token+1
            subpaths.append([_point(matrix, values)]); closed.append(False)
        elif operator in ("l", "lineto", "ls") and len(values) == 2:
            if not subpaths:
                unsafe = True
            else:
                subpaths[-1].append(_point(matrix, values))
        elif operator == "closepath":
            if subpaths:
                closed[-1] = True
        elif re.fullmatch(r"gsave\s+"+NUMBER+r"\s+setlinewidth\s+stroke\s+grestore", code):
            # The exact Abaqus triangle hairline pair saves/restores the path and state.
            continue
        elif operator in ("fill", "eofill"):
            if path_token is not None:
                filled_tokens.add(path_token)
            if not unsafe and len(subpaths) == 1 and len(subpaths[0]) == 3 and closed[0] and max(color)-min(color) > 1e-9:
                triangles.append(subpaths[0])
            subpaths, closed, path_start, path_line, unsafe = [], [], None, None, False
            path_token = None
        elif operator == "stroke":
            pass  # recorded below
        elif operator in ("setlinejoin", "setlinecap", "setmiterlimit") or re.fullmatch(r"\[[\d. \t-]*\]\s+"+NUMBER+r"\s+setdash", code):
            pass
        else:
            # Never infer paths from procedures, glyph text, strings, clipping or arbitrary PS.
            if subpaths:
                unsafe = True
            if " show" in code or "rectfill" in code or "gouraudtriangle" in code:
                subpaths, closed, path_start, path_line, unsafe = [], [], None, None, False
        if operator not in ("stroke", "ls"):
            continue
        if subpaths and not unsafe and path_token not in filled_tokens and all(abs(channel) <= 1e-12 for channel in color):
            match = re.search(r"\b"+operator+r"\b", line)
            paint_start, paint_end = line_offset+match.start(), line_offset+match.end()
            records.append(StrokeRecord(len(records), copy.deepcopy(subpaths), closed[:],
                                        width*_uniform_scale(matrix), color, path_line, number,
                                        path_start, offset, paint_start, paint_end, operator,
                                        payload_offset+paint_start, payload_offset+paint_end))
            record_path_tokens.append(path_token)
        elif unsafe and subpaths:
            warnings.append(f"Unsupported path retained at paint line {number}")
        subpaths, closed, path_start, path_line, unsafe = [], [], None, None, False
        path_token = None
    if stack:
        raise ValueError("Unbalanced explicit gsave/grestore inside viewport")
    # A saved path may be filled only AFTER an earlier stroke (gsave/stroke/
    # grestore/fill). Delay this filter until the whole viewport is inspected.
    records = [record for record, token in zip(records, record_path_tokens) if token not in filled_tokens]
    for index, record in enumerate(records):
        record.index = index
    _uniform_scale(matrix)
    mesh_bounds = tuple(mesh_bounds_pt) if mesh_bounds_pt is not None else _main_surface_bounds(triangles)
    for record in records:
        points = [point for segment in record.subpaths_pt for point in segment]
        if record.paint_start >= annotation_start:
            record.protection_reason = "Abaqus annotation/legend block"
        elif mesh_bounds is None:
            record.protection_reason = "no main colored surface; explicit mesh_bounds_pt required"
        elif len(points) < 2 or record.width_pt <= 0:
            record.protection_reason = "empty/zero-width path"
        elif all(mesh_bounds[0]-0.001 <= x <= mesh_bounds[2]+0.001 and mesh_bounds[1]-0.001 <= y <= mesh_bounds[3]+0.001 for x, y in points):
            record.protected, record.protection_reason = False, ""
    return EpsDocument(source, hashlib.sha256(raw).hexdigest(), ps, payload_offset, payload_length,
                       start, end, matrix, records, mesh_bounds, warnings)


def _overlay(document, paths, default_width):
    inverse = _inverse(document.overlay_matrix)
    scale = _uniform_scale(document.overlay_matrix)
    commands = ["\n% v0.2.0 rebuilt visible mesh -- independent overlay\ngsave\n0 0 0 setrgbcolor\n0 setlinecap 0 setlinejoin [] 0 setdash\n"]
    count = 0
    for item in paths:
        if isinstance(item, dict):
            points = item.get("points_pt", item.get("points"))
            if points is None and "a" in item and "b" in item:
                points = [item["a"], item["b"]]
            width = item.get("width_pt", item.get("width", default_width))
            closed = bool(item.get("closed", False))
        else:
            points, width, closed = item, default_width, False
        if points is None or len(points) < 2 or not math.isfinite(width) or width <= 0:
            raise ValueError("Each overlay needs >=2 finite points and positive width_pt")
        if any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
            raise ValueError("Invalid overlay coordinates")
        transformed = [_point(inverse, point) for point in points]
        commands.append(f"{width/scale:.12g} setlinewidth\nnewpath\n")
        for i, (x, y) in enumerate(transformed):
            commands.append(f"{x:.12g} {y:.12g} {'moveto' if i == 0 else 'lineto'}\n")
        if closed:
            commands.append("closepath\n")
        commands.append("stroke\n"); count += 1
    commands.append("grestore\n")
    return "".join(commands), count


def write_rebuilt_eps(document: EpsDocument, output, remove_ids: Iterable[int],
                      overlay_paths: Sequence = (), *, stroke_width_pt=None):
    """Write a NEW pure-text EPS, changing only specified stroke tokens and appending an overlay.

    Protected records cannot be removed. The output must not already exist. IDs
    are tied to document.source_sha256; a changed source is rejected.
    """
    output = Path(output).resolve()
    if output == document.source or (output.exists() and output.samefile(document.source)):
        raise ValueError("Never overwrite the source EPS")
    if hashlib.sha256(document.source.read_bytes()).hexdigest() != document.source_sha256:
        raise ValueError("Source changed since read_eps; refresh the records/IDs")
    ids = list(remove_ids)
    if any(type(i) is not int for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("remove_ids must be unique integer black-stroke indices")
    if any(i < 0 or i >= len(document.black_strokes) for i in ids):
        raise ValueError("Unknown black-stroke index")
    selected = [document.black_strokes[i] for i in ids]
    protected = [r.index for r in selected if r.protected]
    if protected:
        raise ValueError(f"Refusing to remove protected annotation/uncertain strokes: {protected[:12]}")
    edits = [(r.paint_start, r.paint_end, "lineto newpath" if r.paint_operator == "ls" else "newpath") for r in selected]
    count = 0
    if overlay_paths:
        overlay, count = _overlay(document, overlay_paths, document.default_stroke_width_pt if stroke_width_pt is None else stroke_width_pt)
        edits.append((document.overlay_offset, document.overlay_offset, overlay))
    ps = document.ps
    for start, end, replacement in sorted(edits, reverse=True):
        ps = ps[:start]+replacement+ps[end:]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(ps.encode("latin1"))
    return {"output": str(output), "removed": len(selected), "overlay_paths": count,
            "source_sha256": document.source_sha256, "output_sha256": hashlib.sha256(ps.encode("latin1")).hexdigest(),
            "removed_ids": ids, "only_paint_tokens_replaced": True}


def replace_black_strokes(source, output, remove_ids, overlay_paths=(), *, stroke_width_pt=None, mesh_bounds_pt=None):
    return write_rebuilt_eps(read_eps(source, mesh_bounds_pt=mesh_bounds_pt), output,
                             remove_ids, overlay_paths, stroke_width_pt=stroke_width_pt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source"); parser.add_argument("--list-json")
    parser.add_argument("--output"); parser.add_argument("--remove-json", help="JSON list of black-stroke indices")
    parser.add_argument("--overlay-json", help="JSON list of {points_pt,width_pt} or {a,b,width}")
    parser.add_argument("--stroke-width-pt", type=float)
    parser.add_argument("--mesh-bounds", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    args = parser.parse_args(); document = read_eps(args.source, mesh_bounds_pt=args.mesh_bounds)
    if args.list_json:
        target = Path(args.list_json).resolve()
        if target == document.source:
            parser.error("list-json cannot replace the source")
        with target.open("x", encoding="utf8") as handle:
            json.dump({"summary": document.summary(), "black_strokes": [r.to_dict() for r in document.black_strokes]}, handle, ensure_ascii=False, indent=2)
    if args.output:
        ids = json.loads(Path(args.remove_json).read_text(encoding="utf-8-sig")) if args.remove_json else []
        overlay = json.loads(Path(args.overlay_json).read_text(encoding="utf-8-sig")) if args.overlay_json else []
        print(json.dumps(write_rebuilt_eps(document, args.output, ids, overlay, stroke_width_pt=args.stroke_width_pt), ensure_ascii=False))
    else:
        print(json.dumps(document.summary(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
