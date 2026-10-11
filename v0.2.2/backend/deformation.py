"""Read Abaqus deformation scales without guessing missing display settings.

The EPS annotation is authoritative for the current image. Camera files and
cached frames must not silently replace it, since the same frame may be plotted
with different component scales.
"""
from __future__ import annotations

import math
from numbers import Real
from pathlib import Path
import re


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_MARKER = re.compile(r"\bDeformation\s+Scale\s+Factor\b", re.I)
_UNIFORM = re.compile(_NUMBER)
_COMPONENTS = re.compile(
    r"([xyz])\s*=\s*(" + _NUMBER + r")\s+"
    r"([xyz])\s*=\s*(" + _NUMBER + r")\s+"
    r"([xyz])\s*=\s*(" + _NUMBER + r")", re.I)


def _finite_number(value):
    # bool is a Real in Python, but is never a meaningful display scale.
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("变形倍率必须是有限数值，不能使用字符串、布尔值或空值。")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("变形倍率必须是有限数值。") from exc
    if not math.isfinite(result):
        raise ValueError("变形倍率必须是有限数值，不能使用 NaN 或 Infinity。")
    return result


def _parse_annotation(label, marker):
    tail = label[marker.end():]
    if not tail.lstrip().startswith(":"):
        raise ValueError("EPS 变形倍率标注格式错误：Deformation Scale Factor 后缺少冒号。")
    value = tail.lstrip()[1:].strip()
    if _UNIFORM.fullmatch(value):
        scalar = _finite_number(float(value))
        return "UNIFORM", [scalar, scalar, scalar]
    components = _COMPONENTS.fullmatch(value)
    if components:
        pairs = components.groups()
        axes = [pairs[i].lower() for i in (0, 2, 4)]
        if set(axes) != {"x", "y", "z"}:
            raise ValueError("EPS 分方向变形倍率必须各包含一次 x、y、z，不能缺失或重复。")
        by_axis = {pairs[i].lower(): _finite_number(float(pairs[i+1])) for i in (0, 2, 4)}
        return "NONUNIFORM", [by_axis[axis] for axis in "xyz"]
    raise ValueError("无法完整读取 EPS 变形倍率：应为单个数值，或完整的 x = 数值 y = 数值 z = 数值。")


def read_deformation_from_labels(labels, required=True):
    """Return one unambiguous uniform/nonuniform EPS deformation annotation.

    Identical repeated annotations (including equivalent numeric formatting)
    are accepted. Invalid annotations are errors even when ``required=False``;
    that option only permits complete absence of a deformation annotation.
    Axis labels may be in any order; factors are always returned as X, Y, Z.
    """
    found = None
    for label in labels:
        if not isinstance(label, str):
            raise ValueError("EPS 标注必须是字符串。")
        marker = _MARKER.search(label)
        if marker is None:
            continue
        mode, factors = _parse_annotation(label, marker)
        if found is not None and (found["mode"] != mode or found["factors"] != factors):
            raise ValueError("EPS 含有相互冲突的变形倍率标注，无法唯一确定当前视图的倍率。")
        if found is None:
            found = {"mode": mode, "factors": factors, "annotation": label,
                     "source": "EPS annotation"}
    if found is None and required:
        raise ValueError("EPS 缺少 Deformation Scale Factor 标注；请在 Abaqus 中显示变形倍率后重新导出。")
    return found


def read_eps_deformation(eps_path):
    """Read PS literal labels inside the declared payload, not comments/preview."""
    # Local imports keep the metadata module free to use this parser itself.
    from eps_io import extract_postscript
    from odb_cache import _ps_strings

    ps, _, _ = extract_postscript(Path(eps_path).read_bytes())
    return read_deformation_from_labels(_ps_strings(ps))


def factors_from_fit(fit):
    """Return X/Y/Z scales, preferring v0.2.2 data over a legacy scalar.

    Malformed new data never falls back to a legacy value. Supporting the old
    scalar permits previously validated v0.2.0 fit records to remain readable.
    """
    if not isinstance(fit, dict):
        raise ValueError("配准结果必须是包含变形倍率的对象。")
    if "deformation" in fit:
        deformation = fit["deformation"]
        if not isinstance(deformation, dict):
            raise ValueError("配准结果 deformation 必须是包含 mode 和 factors 的对象。")
        mode = deformation.get("mode")
        if mode not in ("UNIFORM", "NONUNIFORM"):
            raise ValueError("未知变形倍率模式；应为 UNIFORM 或 NONUNIFORM。")
        factors = deformation.get("factors")
        if not isinstance(factors, (list, tuple)) or len(factors) != 3:
            raise ValueError("变形倍率 factors 必须依次包含 X、Y、Z 三个数值。")
        result = tuple(_finite_number(value) for value in factors)
        if mode == "UNIFORM" and not (result[0] == result[1] == result[2]):
            raise ValueError("UNIFORM 模式的 X、Y、Z 变形倍率必须相同。")
        return result
    if "deformationScale" in fit:
        value = _finite_number(fit["deformationScale"])
        return value, value, value
    raise ValueError("配准结果缺少变形倍率，不能默认使用 1。")
