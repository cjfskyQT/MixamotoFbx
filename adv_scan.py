"""
ADV / mGear / Advanced Skeleton controller scanner.

Reads a Maya .ma file and returns a flat list of every transform
that looks like a rig controller (FK<Bone>_<Side>, IK<Bone>_<Side>,
FKIK<Bone>_<Side>, Pole<Bone>_<Side> etc.).

This is *scanning*, not loading into Maya: it parses the .ma text
directly so it can run from `mayapy` or any plain Python without
Maya's commandPort or scene being open.  Useful for:

* auto-populating the matcher dialog without launching Maya
* spotting rigged rigs in headless CI
* documenting a rig without opening the .ma

Usage::

    from .adv_scan import scan_ma_file
    ctrls = scan_ma_file(r"D:\path\to\rig.ma")
    for c in ctrls: print(c)

Returned objects are :class:`AdvController` dataclasses.

.. warning::

   Never open a downloaded .ma file in Maya without first scanning
   it for malicious ``scriptNode`` payloads.  See ``README.md`` for
   details.

Naming conventions supported
-----------------------------

Both Advanced Skeleton and mGear rigs share the controller naming
style ``FK<Bone>_<Side>`` / ``IK<Bone>_<Side>`` / ``FKIK<Bone>_<Side>``
and ``Pole<Bone>_<Side>``.  The scanner treats them identically -
the user's "ADV"  in this project refers to either rig family, since
the retargeting logic is the same.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Set


# ---------------------------------------------------------------------------
# Naming patterns.  Real ADV/mGear controller names observed on a
# production rig (mGear 4.x, Maya 2025) include:
#   FKShoulder_R, FKElbow_R, FKWrist_R, FKHip_R, FKKnee_R, FKAnkle_R,
#   FKToes_R, FKRoot_M, FKSpine1_M, FKSpine1Part1_M, FKChest_M,
#   FKNeck_M, FKHead_M, FKIKArm_R, FKIKArm_L, FKIKLeg_R, FKIKLeg_L,
#   FKIKSpine_M, PoleArm_R, IKArm_R, IKLeg_R, ...
# ---------------------------------------------------------------------------

ADV_PATTERNS = [
    # Master control
    r"^Main$",
    r"^RootX_M$",
    # FK chain  (FKShoulder_R, FKNeck_M, FKSpine1Part2_M, etc.)
    r"^FK[A-Z][A-Za-z0-9]*_(M|L|R)$",
    # IK chain
    r"^IK[A-Z][A-Za-z0-9]*_(M|L|R)$",
    # Pole vector  (PoleArm_R, PoleLeg_L)
    r"^Pole[A-Z][A-Za-z0-9]*_(M|L|R)$",
    # FK/IK switch controller  (FKIKArm_R, FKIKSpine_M)
    r"^FKIK[A-Z][A-Za-z0-9]*_(M|L|R)$",
]


# Helpers / guides / drive joints that we *don't* want to surface as
# user-controllable targets.  Real ADV/mGear controllers do not match
# these prefixes - they live under the parent controllers and exist
# purely as motion-system plumbing.
HELPER_PREFIXES = (
    "FKOffset", "FKExtra", "FKX", "FKPS", "FKHS", "FKBM",
    "IKOffset", "IKExtra", "IKX", "IKPS", "IKHS", "IKBM",
    "IKMessure", "IKScaler", "IKAc", "IKSp", "IKcv",
    "IKhybrid", "IKfkAligned", "IKLegFootRock", "IKPoleVector",
    "IKCurve", "IKFollow", "IKStatic", "PoleVector",
    "AlignIKTo", "Inbetween", "HipSwing", "SDKD",
)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class AdvController:
    name: str
    kind: str            # "FK" | "IK" | "Pole" | "FKIK" | "Main" | "Other"
    side: str            # "M" | "L" | "R" | ""

    @property
    def attr(self) -> str:
        """Likely attribute name suffix used by ADV/mGear ('FKIKBlend' etc.)."""
        return "FKIKBlend"


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------

CREATE_TRANSFORM_RE = re.compile(
    r'createNode\s+transform\b[^;\r\n]*?\s-(?:n|name)\s+"([^"]+)"',
)


def _classify(name: str) -> AdvController:
    full_name = name
    name = name.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
    if name == "Main":
        return AdvController(name=full_name, kind="Main", side="M")
    if name == "RootX_M":
        return AdvController(name=full_name, kind="Root", side="M")
    if name.startswith("FKIK"):
        kind = "FKIK"
    elif name.startswith("FK"):
        kind = "FK"
    elif name.startswith("Pole"):
        kind = "Pole"
    elif name.startswith("IK"):
        kind = "IK"
    else:
        return AdvController(name=full_name, kind="Other", side="")

    # Last underscore-separated component is the side (M / L / R)
    if "_" in name:
        side = name.rsplit("_", 1)[-1]
        if side not in ("M", "L", "R"):
            side = ""
    else:
        side = ""
    return AdvController(name=full_name, kind=kind, side=side)


def _is_helper(name: str) -> bool:
    """Return True if ``name`` is a guide / drive / plumbing transform."""
    if name.endswith("Shape"):
        return True
    if "Constraint" in name:
        return True
    if any(name.startswith(p) for p in HELPER_PREFIXES):
        return True
    return False


def scan_ma_file(path: str) -> List[AdvController]:
    """Return every controller-shaped transform name found in a .ma file.

    Uses ``latin-1`` decoding so files with embedded GBK/CP936 Chinese
    characters (typical for rigs from Chinese studios) still parse;
    ASCII controller names survive that round-trip intact.
    """
    with open(path, "r", encoding="latin-1", errors="replace") as fh:
        text = fh.read()
    return scan_ma_text(text)


def scan_ma_text(text: str) -> List[AdvController]:
    """Same as :func:`scan_ma_file` but on raw text (for tests / fuzzing)."""
    out: List[AdvController] = []
    seen: Set[str] = set()
    for m in CREATE_TRANSFORM_RE.finditer(text):
        name = m.group(1)
        leaf = name.rsplit(":", 1)[-1]
        if _is_helper(leaf):
            continue
        if not any(re.match(p, leaf) for p in ADV_PATTERNS):
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(_classify(name))
    return out


def filter_by_kind(ctrls: Iterable[AdvController], kind: str) -> List[str]:
    """Return just the names of every controller of a given kind."""
    return [c.name for c in ctrls if c.kind == kind]


# ---------------------------------------------------------------------------
# Mixamo -> ADV / mGear FK controller mapping
#
# ADV control names. RootX_M supplies rigid whole-pelvis motion; FKRoot_M
# can distribute spine rotation and lock leg origins, so it is not equivalent.
# Scapula and Shoulder remain separate controls.
# ---------------------------------------------------------------------------

MIXAMO_TO_ADV_FK = {
    # Spine chain
    "Hips":           "RootX_M",
    "Spine":          "FKSpine1_M",
    "Spine1":         "FKSpine1Part1_M",
    "Spine2":         "FKChest_M",
    # Head
    "Neck":           "FKNeck_M",
    "Head":           "FKHead_M",
    # Left arm — Scapula and Shoulder are separate controllers on
    # ADV/mGear rigs (we previously merged them into one controller
    # which caused the last write to silently overwrite the first).
    "LeftShoulder":   "FKScapula_L",
    "LeftArm":        "FKShoulder_L",
    "LeftForeArm":    "FKElbow_L",
    "LeftHand":       "FKWrist_L",
    # Right arm
    "RightShoulder":  "FKScapula_R",
    "RightArm":       "FKShoulder_R",
    "RightForeArm":   "FKElbow_R",
    "RightHand":      "FKWrist_R",
    # Left leg
    "LeftUpLeg":      "FKHip_L",
    "LeftLeg":        "FKKnee_L",
    "LeftFoot":       "FKAnkle_L",
    "LeftToeBase":    "FKToes_L",
    # Right leg
    "RightUpLeg":     "FKHip_R",
    "RightLeg":       "FKKnee_R",
    "RightFoot":      "FKAnkle_R",
    "RightToeBase":   "FKToes_R",
}


# Names of the FK/IK switch controllers present on every ADV / mGear rig.
# Setting ``FKIKBlend`` to 0 forces FK mode on the corresponding limb.
ADV_FKIK_SWITCHES = (
    "FKIKArm_R",
    "FKIKArm_L",
    "FKIKLeg_R",
    "FKIKLeg_L",
    # Spine (mGear rigs expose this too):
    "FKIKSpine_M",
)


__all__ = [
    "AdvController",
    "ADV_FKIK_SWITCHES",
    "MIXAMO_TO_ADV_FK",
    "filter_by_kind",
    "scan_ma_file",
    "scan_ma_text",
]
