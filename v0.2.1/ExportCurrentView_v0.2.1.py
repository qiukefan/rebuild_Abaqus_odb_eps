# -*- coding: utf-8 -*-
"""Abaqus/CAE File > Run Script: export the current camera and deformation.

Restore the view used to export the EPS before running this helper. Camera
fields remain at the JSON root for compatibility with earlier releases. The
deformation record is diagnostic: reconstruction reads the current EPS label
as the authoritative deformation scale, rather than reusing this saved value.
"""
import json
import math
import os
from abaqus import session, getInput


def _finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Deformation scale factors must be finite numbers.')
    return value


def collect_viewport_settings(viewport):
    """Read only the scale field used by the active mode; never change a view."""
    displayed = getattr(viewport, 'displayedObject', None)
    if (displayed is None or not hasattr(displayed, 'steps')
            or not hasattr(displayed, 'rootAssembly')):
        raise ValueError('The current viewport must display an ODB result.')
    view = viewport.view
    fields = ('cameraPosition', 'cameraTarget', 'cameraUpVector', 'width', 'height', 'viewOffsetX', 'viewOffsetY', 'nearPlane', 'farPlane')
    result = {name: getattr(view, name) for name in fields}
    result['projection'] = str(view.projection)
    options = viewport.odbDisplay.commonOptions
    mode = str(options.deformationScaling).upper()
    if mode == 'AUTO':
        factors = [_finite(options.autoDeformationScaleValue)] * 3
    elif mode == 'UNIFORM':
        factors = [_finite(options.uniformScaleFactor)] * 3
    elif mode == 'NONUNIFORM':
        factors = list(options.nonuniformScaleFactor)
        if len(factors) != 3:
            raise ValueError('Nonuniform deformation scaling requires X, Y, Z.')
        factors = [_finite(value) for value in factors]
    else:
        raise ValueError('Unsupported deformation scaling mode: ' + mode)
    result['deformation'] = {
        'mode': mode, 'factors': factors, 'source': 'Abaqus viewport',
    }
    return result


def write_new_json(path, result):
    # Validate/serialize before creating a file. In particular, JSON must not
    # silently contain NaN/Infinity in either the camera or the scale record.
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    created = False
    try:
        # Exclusive creation protects a previously exported file from changes.
        with open(path, 'x', encoding='utf-8') as handle:
            created = True
            handle.write(payload + '\n')
    except Exception:
        if created:
            # A failed write must not leave a truncated file that looks usable.
            os.remove(path)
        raise


def main():
    suggested = os.path.join(os.path.expanduser('~'), 'Desktop', 'camera_v0.2.1.json')
    path = getInput('Save camera and deformation JSON (same viewport as EPS):', suggested)
    if not path:
        return
    try:
        viewport = session.viewports[session.currentViewportName]
        result = collect_viewport_settings(viewport)
        write_new_json(path, result)
    except Exception as exc:
        raise RuntimeError('Camera/deformation export failed: ' + str(exc)) from exc
    print('Camera and deformation saved: ' + path)
    print('Reconstruction uses the deformation scale label in the current EPS.')


if __name__ == '__main__':
    main()
