"""C3D20 surface topology and vector hidden-line removal. Coordinates are points + depth.

No raster tracing, gap extension, or reconstruction of missing finite-element topology.
Smaller depth is nearer the camera. Adjacent faces cannot hide their own shared edge.
"""
from collections import defaultdict
import numpy as np

FACES = (
    (0, 3, 2, 1, 11, 10, 9, 8),
    (4, 5, 6, 7, 12, 13, 14, 15),
    (0, 1, 5, 4, 8, 17, 12, 16),
    (1, 2, 6, 5, 9, 18, 13, 17),
    (2, 3, 7, 6, 10, 19, 14, 18),
    (3, 0, 4, 7, 11, 16, 15, 19),
)


def exterior_topology(instance):
    faces = defaultdict(list)
    for element in instance['elements']:
        if element['type'] not in ('C3D20', 'C3D20R'):
            raise ValueError('Unsupported element: ' + element['type'])
        c = element['connectivity']
        for f in FACES:
            labels = tuple(c[i] for i in f)
            faces[tuple(sorted(labels[:4]))].append(labels)
    if any(len(v) > 2 for v in faces.values()):
        raise ValueError('Non-manifold volume mesh')
    outer = [v[0] for v in faces.values() if len(v) == 1]
    edges = {}
    for face_id, f in enumerate(outer):
        for i in range(4):
            a, b, mid = f[i], f[(i + 1) % 4], f[i + 4]
            key = tuple(sorted((a, b)))
            if key not in edges:
                edges[key] = {'nodes': (a, mid, b), 'faces': []}
            elif edges[key]['nodes'][1] != mid:
                raise ValueError('Nonconforming midside nodes')
            edges[key]['faces'].append(face_id)
    if any(len(e['faces']) != 2 for e in edges.values()):
        raise ValueError('The extracted surface is not closed')
    return outer, list(edges.values())


def quad_weights(x, y):
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    return np.array([.25*(1+x*a)*(1+y*b)*(x*a+y*b-1) for a, b in corners] +
                    [.5*(1-x*x)*(1-y), .5*(1+x)*(1-y*y),
                     .5*(1-x*x)*(1+y), .5*(1-x)*(1-y*y)])


def discretize(faces, edges, coordinates, project, subdivisions=4):
    """The face and edge subdivisions share exactly the same boundary samples."""
    triangles, owners, paths = [], [], []
    for face_id, labels in enumerate(faces):
        xyz = np.array([coordinates[i] for i in labels])
        samples = np.array([[quad_weights(x, y) @ xyz for x in np.linspace(-1, 1, subdivisions+1)]
                            for y in np.linspace(-1, 1, subdivisions+1)])
        samples = project(samples.reshape(-1, 3)).reshape(subdivisions+1, subdivisions+1, 3)
        for j in range(subdivisions):
            for i in range(subdivisions):
                a, b, c, d = samples[j, i], samples[j, i+1], samples[j+1, i+1], samples[j+1, i]
                triangles.extend(((a, b, c), (a, c, d)))
                owners.extend((face_id, face_id))
    for edge_id, edge in enumerate(edges):
        xyz = np.array([coordinates[i] for i in edge['nodes']])
        t = np.linspace(0, 1, subdivisions+1)
        weights = np.column_stack(((1-t)*(1-2*t), 4*t*(1-t), t*(2*t-1)))
        paths.append({'edge': edge_id, 'nodes': edge['nodes'], 'faces': edge['faces'],
                      'points': project(weights @ xyz)})
    return np.array(triangles), np.array(owners), paths


class Occluders:
    def __init__(self, triangles, owners, cell_size=8.0, depth_tolerance=1e-5):
        self.depth_tolerance = depth_tolerance
        self.cell_size = cell_size
        self.grid = defaultdict(list)
        a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
        u, v = b[:, :2]-a[:, :2], c[:, :2]-a[:, :2]
        det = u[:, 0]*v[:, 1]-u[:, 1]*v[:, 0]
        keep = abs(det) > 1e-12
        a, b, c, det = a[keep], b[keep], c[keep], det[keep]
        self.triangles = np.array([a, b, c]).transpose(1, 0, 2)
        self.owners = owners[keep]
        # Barycentric-coordinate affine coefficients, including depth affine plane.
        u, v = b[:, :2]-a[:, :2], c[:, :2]-a[:, :2]
        self.coeff = np.empty((len(a), 3, 3))
        self.coeff[:, 1, :2] = np.column_stack((v[:, 1], -v[:, 0]))/det[:, None]
        self.coeff[:, 2, :2] = np.column_stack((-u[:, 1], u[:, 0]))/det[:, None]
        self.coeff[:, 1:, 2] = -np.einsum('nij,nj->ni', self.coeff[:, 1:, :2], a[:, :2])
        self.coeff[:, 0] = np.array([0, 0, 1])-self.coeff[:, 1]-self.coeff[:, 2]
        self.depth = np.einsum('ni,nij->nj', self.triangles[:, :, 2], self.coeff)
        lows = np.floor(self.triangles[:, :, :2].min(axis=1)/cell_size).astype(int)
        highs = np.floor(self.triangles[:, :, :2].max(axis=1)/cell_size).astype(int)
        for idx, (low, high) in enumerate(zip(lows, highs)):
            for x in range(low[0], high[0]+1):
                for y in range(low[1], high[1]+1):
                    self.grid[x, y].append(idx)

    def visible_intervals(self, a, b, skip_faces=()):
        low = np.floor(np.minimum(a[:2], b[:2])/self.cell_size).astype(int)
        high = np.floor(np.maximum(a[:2], b[:2])/self.cell_size).astype(int)
        indices = set()
        for x in range(low[0], high[0]+1):
            for y in range(low[1], high[1]+1):
                indices.update(self.grid.get((x, y), ()))
        if not indices:
            return [(0.0, 1.0)]
        ids = np.array(list(indices), dtype=int)
        if skip_faces:
            ids = ids[~np.isin(self.owners[ids], skip_faces)]
        if not len(ids):
            return [(0.0, 1.0)]
        p, d = np.r_[a[:2], 1], np.r_[b[:2]-a[:2], 0]
        at = np.einsum('nij,j->ni', self.coeff[ids], p)
        slope = np.einsum('nij,j->ni', self.coeff[ids], d)
        # Fourth inequality: segment depth minus triangle depth exceeds tolerance.
        at = np.column_stack((at, a[2]-self.depth[ids]@p-self.depth_tolerance))
        slope = np.column_stack((slope, b[2]-a[2]-self.depth[ids]@d))
        lo, hi = np.zeros(len(ids)), np.ones(len(ids))
        valid = np.ones(len(ids), dtype=bool)
        for k in range(4):
            s, v = slope[:, k], at[:, k]
            parallel = abs(s) < 1e-14
            valid &= ~(parallel & (v < 0))
            t = np.divide(-v, s, out=np.zeros_like(v), where=~parallel)
            lo = np.where(s > 1e-14, np.maximum(lo, t), lo)
            hi = np.where(s < -1e-14, np.minimum(hi, t), hi)
        valid &= hi > lo+1e-10
        intervals = sorted(zip(lo[valid], hi[valid]))
        visible, cursor = [], 0.0
        for start, end in intervals:
            if start > cursor+1e-9:
                visible.append((cursor, float(start)))
            cursor = max(cursor, float(end))
        if cursor < 1-1e-9:
            visible.append((cursor, 1.0))
        return visible


def visible_paths(triangles, owners, paths, progress=None, depth_tolerance=1e-5):
    index = Occluders(triangles, owners, depth_tolerance=depth_tolerance)
    result = []
    for i, path in enumerate(paths):
        runs, current = [], []
        points = path['points']
        for a, b in zip(points[:-1], points[1:]):
            intervals = index.visible_intervals(a, b, path['faces'])
            if not intervals and current:
                runs.append(current); current = []
            for lo, hi in intervals:
                start, end = a+(b-a)*lo, a+(b-a)*hi
                if current and np.linalg.norm(np.array(current[-1])-start[:2]) < 1e-7:
                    current.append(end[:2].tolist())
                else:
                    if current: runs.append(current)
                    current = [start[:2].tolist(), end[:2].tolist()]
                if hi < 1-1e-9:
                    runs.append(current); current = []
        if current: runs.append(current)
        for run in runs:
            if np.linalg.norm(np.diff(run, axis=0), axis=1).sum() > 1e-7:
                result.append({'edge': path['edge'], 'nodes': path['nodes'], 'points': run})
        if progress and (i % 100 == 0 or i == len(paths)-1):
            progress(i+1, len(paths))
    return result


def clip_paths_to_rect(paths, bounds):
    """Clip actual vector coordinates, because Illustrator may ignore outer EPS clips."""
    xmin, ymin, xmax, ymax = bounds
    result = []
    for path in paths:
        current = []
        def flush():
            nonlocal current
            if len(current) >= 2:
                result.append(dict(path, points=current))
            current = []
        for a, b in zip(path['points'][:-1], path['points'][1:]):
            a, b = np.asarray(a), np.asarray(b)
            d = b-a
            lo, hi = 0., 1.
            for axis, lower, upper in ((0, xmin, xmax), (1, ymin, ymax)):
                if abs(d[axis]) < 1e-14:
                    if a[axis] < lower or a[axis] > upper: hi = -1
                else:
                    t1, t2 = (lower-a[axis])/d[axis], (upper-a[axis])/d[axis]
                    lo, hi = max(lo, min(t1,t2)), min(hi, max(t1,t2))
            if hi <= lo+1e-12:
                flush(); continue
            start, end = a+lo*d, a+hi*d
            if current and np.linalg.norm(np.asarray(current[-1])-start) < 1e-7:
                current.append(end.tolist())
            else:
                flush(); current = [start.tolist(), end.tolist()]
            if hi < 1-1e-12: flush()
        flush()
    return result
