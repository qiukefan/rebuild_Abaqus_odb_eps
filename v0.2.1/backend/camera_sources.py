"""Read camera candidates from local export logs; never execute their Python code."""
import ast
import json
import re
from pathlib import Path

FIELDS = {'cameraPosition', 'cameraTarget', 'cameraUpVector', 'projection',
          'width', 'height', 'viewOffsetX', 'viewOffsetY', 'nearPlane', 'farPlane'}


def cameras_in_text(text):
    found, current = [], {}
    # Replay logs can contain non-Python headers. Extract bounded method calls only.
    for match in re.finditer(r'\.view\.setValues\s*\(([^)]*)\)', text):
        # Parentheses also enclose tuple values: scan balanced parens, including strings.
        start = text.find('(', match.start())
        balance, quote, escaped, end = 1, '', False, start+1
        while end < len(text) and balance:
            ch = text[end]
            if quote:
                if escaped: escaped = False
                elif ch == '\\': escaped = True
                elif ch == quote: quote = ''
            elif ch in ('"', "'"): quote = ch
            elif ch == '(': balance += 1
            elif ch == ')': balance -= 1
            end += 1
        if balance: continue
        try:
            call = ast.parse('camera'+text[start:end], mode='eval').body
            values = {}
            for keyword in call.keywords:
                if keyword.arg not in FIELDS: continue
                if keyword.arg == 'projection' and isinstance(keyword.value, ast.Name):
                    values[keyword.arg] = keyword.value.id
                else:
                    values[keyword.arg] = ast.literal_eval(keyword.value)
            current.update(values)
            if {'cameraPosition','cameraTarget','cameraUpVector'} <= current.keys():
                found.append(dict(current))
        except (ValueError, SyntaxError, TypeError):
            continue
    return found


def discover(eps_path, odb_path=None, explicit_path=None, cache_dir=None):
    candidates, seen = [], set()
    def add(data, source):
        if isinstance(data, dict):
            data = data.get('savedView', data)
            if {'cameraPosition','cameraTarget','cameraUpVector'} <= data.keys():
                # Only perspective is supported by the verified v0.2.1 fitter.
                data = {k:v for k,v in data.items() if k in FIELDS}
                data.setdefault('projection', 'PERSPECTIVE')
                key = json.dumps({k:data[k] for k in ('cameraPosition','cameraTarget','cameraUpVector','projection')}, sort_keys=True)
                if key not in seen:
                    seen.add(key); candidates.append((data, str(source)))
    def read(path):
        try:
            if path.stat().st_size > 8*1024*1024: return
            text = path.read_text(encoding='utf-8-sig', errors='replace')
            if path.suffix.lower() == '.json': add(json.loads(text), path)
            else:
                for data in list(reversed(cameras_in_text(text)))[:10]: add(data, path)
        except (OSError, ValueError): pass
    if explicit_path:
        read(Path(explicit_path))
        if not candidates: raise ValueError('无法读取相机配置：'+str(explicit_path))
    if cache_dir:
        folder = Path(cache_dir)/'cameras'
        if folder.is_dir():
            for p in sorted(folder.glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:20]: read(p)
    for source in (eps_path, odb_path):
        if not source: continue
        folder = Path(source).resolve().parent
        for p in (folder/'abaqus.rpy', folder/'abaqus.rpy.1', folder/'camera.json', folder/'view.json'):
            if p.is_file(): read(p)
    # A seed is only a candidate; every accepted view must match hundreds of actual EPS strokes.
    read(Path(__file__).resolve().parents[1]/'resources/camera_seed.json')
    return candidates
