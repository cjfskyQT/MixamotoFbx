"""FBX metadata and curves read by an isolated installed Maya runtime.

The standalone Autodesk FBX Python SDK is not required. Reads happen in
an isolated scene; existing user scenes and input files are not modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

class FbxUnavailable(RuntimeError):
    """Raised when the Autodesk FBX Python SDK binding is not importable."""


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class FbxBone:
    name: str
    parent: Optional[str] = None
    # rest-pose local transform (translation / rotation in degrees / scale)
    rest_translation: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    rest_rotation_euler: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    rest_scale: Tuple[float, float, float] = (1.0, 1.0, 1.0)


@dataclass
class AnimCurve:
    """Single animated property on a single bone.

    ``frames`` is the sorted list of frame indices (in source time).
    ``values`` holds the corresponding numeric values.
    """
    bone: str
    prop: str               # "T" | "R" | "S"
    axis: str               # "x" | "y" | "z"
    frames: List[float] = field(default_factory=list)
    values: List[float] = field(default_factory=list)


@dataclass
class FbxAnim:
    start: float = 0.0
    stop: float = 0.0
    fps: float = 30.0
    stacks: int = 0         # number of FbxAnimStack found
    layers: int = 0         # total FbxAnimLayer across stacks
    curves: List[AnimCurve] = field(default_factory=list)
    source_path: str = ""


@dataclass
class FbxSkeleton:
    bones: Dict[str, FbxBone] = field(default_factory=dict)
    root: Optional[str] = None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _require_fbx():
    from .maya_runtime import find_mayapy
    return find_mayapy()


def _sample_curve(curve: AnimCurve, frame: float) -> Optional[float]:
    """Linear-interp a single AnimCurve at ``frame``; ``None`` when the
    curve doesn't cover that frame."""
    if not curve.frames:
        return None
    fs = curve.frames
    vs = curve.values
    if frame < fs[0] or frame > fs[-1]:
        return None
    lo, hi = 0, len(fs) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if fs[mid] <= frame:
            lo = mid
        else:
            hi = mid
    if fs[lo] == frame:
        return vs[lo]
    f0, f1 = fs[lo], fs[hi]
    if f1 == f0:
        return vs[lo]
    t = (frame - f0) / (f1 - f0)
    return vs[lo] + (vs[hi] - vs[lo]) * t


def find_curve(curves: Iterable[AnimCurve],
               bone: str, prop: str, axis: str) -> Optional[AnimCurve]:
    """Linear-scan lookup; shared by both writers."""
    from .bone_matcher import leaf_name
    matches = [c for c in curves if leaf_name(c.bone) == leaf_name(bone) and c.prop == prop and c.axis == axis]
    if len(matches) > 1:
        raise ValueError('Ambiguous animation curve: '+bone)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Convenience: load everything in one call
# ---------------------------------------------------------------------------

@dataclass
class FbxDocument:
    path: str
    skeleton: FbxSkeleton
    anim: FbxAnim


def load(path: str) -> FbxDocument:
    from .maya_runtime import run_job
    try:
        data = run_job({'operation':'inspect', 'path':path})
    except RuntimeError as exc:
        raise FbxUnavailable(str(exc)) from exc
    skeleton = FbxSkeleton(bones={name:FbxBone(**bone) for name,bone in data['bones'].items()},root=data['root'])
    anim = FbxAnim(start=data['start'],stop=data['stop'],fps=data['fps'],
                   curves=[AnimCurve(**curve) for curve in data['curves']],source_path=path)
    return FbxDocument(path=path,skeleton=skeleton,anim=anim)
