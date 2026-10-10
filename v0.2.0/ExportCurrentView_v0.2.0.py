# -*- coding: utf-8 -*-
"""Optional Abaqus/CAE File > Run Script helper. Exports camera only."""
import json
import os
from abaqus import session, getInput

viewport = session.viewports[session.currentViewportName]
view = viewport.view
suggested = os.path.join(os.path.expanduser('~'), 'Desktop', 'camera_v0.2.0.json')
path = getInput('Save current viewport camera JSON (same view as EPS):', suggested)
if path:
    fields = ('cameraPosition', 'cameraTarget', 'cameraUpVector', 'width', 'height', 'viewOffsetX', 'viewOffsetY', 'nearPlane', 'farPlane')
    result = {name: getattr(view, name) for name in fields}
    result['projection'] = str(view.projection)
    # Exclusive creation protects a previously exported camera from replacement.
    with open(path, 'x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print('Camera saved: ' + path)
