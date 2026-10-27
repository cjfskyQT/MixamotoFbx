"""
Bone matching: turn a mixamo skeleton into a target skeleton name map.

Three entry points:

* :func:`auto_match_adv`     - mixamo names already match ADV / HIK
  standard names one-for-one.
* :func:`auto_match_by_name` - case-insensitive / fuzzy fall-back.
* :func:`manual_map`         - caller-supplied dict of mixamo->target.

Each ``MixamoTarget`` has an optional ``enabled`` flag so the UI's
"leave blank = skip" behaviour maps naturally to "skip this bone".

Naming-space handling
---------------------

Mixamo character FBX exports ship with the bone names qualified by a
``mixamorig:`` namespace prefix, e.g. ``mixamorig:Hips``,
``mixamorig:LeftArm``.  Maya HIK / ADV / mGear rigs usually live
inside a per-character namespace, e.g. ``myCharacter_v001:Root_M``
or ``character1:Spine1``.  The matcher strips namespace prefixes on
both sides before comparing, so the identity map keeps working
regardless of which namespace either side lives in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple


# ---------------------------------------------------------------------------
# Mixamo / Maya HIK / HumanIK canonical names.
# ---------------------------------------------------------------------------

MIXAMO_CANONICAL = (
    "Hips",
    "Spine", "Spine1", "Spine2",
    "Neck", "Head",
    "LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand",
    "RightShoulder", "RightArm", "RightForeArm", "RightHand",
    "LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase",
    "RightUpLeg", "RightLeg", "RightFoot", "RightToeBase",
    "LeftHandThumb1", "LeftHandThumb2", "LeftHandThumb3", "LeftHandThumb4",
    "LeftHandIndex1", "LeftHandIndex2", "LeftHandIndex3", "LeftHandIndex4",
    "LeftHandMiddle1", "LeftHandMiddle2", "LeftHandMiddle3", "LeftHandMiddle4",
    "LeftHandRing1", "LeftHandRing2", "LeftHandRing3", "LeftHandRing4",
    "LeftHandPinky1", "LeftHandPinky2", "LeftHandPinky3", "LeftHandPinky4",
    "RightHandThumb1", "RightHandThumb2", "RightHandThumb3", "RightHandThumb4",
    "RightHandIndex1", "RightHandIndex2", "RightHandIndex3", "RightHandIndex4",
    "RightHandMiddle1", "RightHandMiddle2", "RightHandMiddle3", "RightHandMiddle4",
    "RightHandRing1", "RightHandRing2", "RightHandRing3", "RightHandRing4",
    "RightHandPinky1", "RightHandPinky2", "RightHandPinky3", "RightHandPinky4",
)


# ---------------------------------------------------------------------------
# Namespace helpers
# ---------------------------------------------------------------------------

def split_namespace(full_name: str) -> Tuple[str, str]:
    """Return ``(namespace, leaf)`` for a fully-qualified Maya node name.

    Examples::

        "Hips"                   -> ("",   "Hips")
        "mixamorig:Hips"         -> ("mixamorig", "Hips")
        "character1:Spine1"      -> ("character1", "Spine1")
        "a:b:c:Hips"             -> ("a:b:c", "Hips")   # nested namespaces
    """
    full_name = full_name.rsplit("|", 1)[-1]
    if ":" in full_name:
        ns, _, leaf = full_name.rpartition(":")
        return ns, leaf
    return "", full_name


def leaf_name(full_name: str) -> str:
    """Return just the leaf part of ``full_name`` (no namespace)."""
    return split_namespace(full_name)[1]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MixamoTarget:
    mixamo: str         # leaf name (no namespace) — canonical
    mixamo_full: str    # fully qualified name as it appeared on disk
    target: str = ""    # full target node name as it appears on disk
    enabled: bool = False

    def is_active(self) -> bool:
        return self.enabled and bool(self.target)


@dataclass
class BoneMap:
    """A mapping from mixamo leaf names to target node names.

    Keys are leaf names (e.g. ``Hips``) so the same map can be reused
    across characters with different namespaces.
    """

    entries: Dict[str, MixamoTarget] = field(default_factory=dict)
    target_namespace: str = ""
    source_namespace: str = ""
    target_signature: str = ""

    # ----- Construction helpers ---------------------------------------

    @classmethod
    def from_mixamo_names(cls, names: Iterable[str]) -> "BoneMap":
        m = cls()
        for full in names:
            ns, leaf = split_namespace(full)
            m.source_namespace = ns
            m.entries[leaf] = MixamoTarget(mixamo=leaf, mixamo_full=full)
        return m

    # ----- Lookup -----------------------------------------------------

    def active_targets(self) -> Dict[str, str]:
        """Return ``{mixamo_leaf: target_full}`` for every enabled row.

        The mixamo side is always the leaf name; the target side keeps
        the original full name so writers can find the actual node.
        """
        return {e.mixamo: e.target
                for e in self.entries.values()
                if e.is_active()}

    def set_target(self, mixamo_leaf: str, target_full: str) -> None:
        mixamo_leaf = leaf_name(mixamo_leaf)
        if mixamo_leaf not in self.entries:
            self.entries[mixamo_leaf] = MixamoTarget(
                mixamo=mixamo_leaf, mixamo_full=mixamo_leaf)
        row = self.entries[mixamo_leaf]
        row.target = target_full
        row.enabled = bool(target_full)
        ns, _ = split_namespace(target_full)
        if ns:
            self.target_namespace = ns


# ---------------------------------------------------------------------------
# Auto matchers
# ---------------------------------------------------------------------------

ADV_JOINTS = {
    'Hips': 'Root_M', 'Spine': 'Spine1_M', 'Spine1': 'Spine1Part1_M',
    'Spine2': 'Chest_M', 'Neck':'Neck_M', 'Head':'Head_M',
}
for side, suffix in [('Left','L'),('Right','R')]:
    for mixamo, adv in [('Shoulder','Scapula'),('Arm','Shoulder'),('ForeArm','Elbow'),
                         ('Hand','Wrist'),('UpLeg','Hip'),('Leg','Knee'),
                         ('Foot','Ankle'),('ToeBase','Toes')]:
        ADV_JOINTS[side+mixamo] = adv+'_'+suffix
    for finger in ['Thumb','Index','Middle','Ring','Pinky']:
        for segment in range(1,4):
            ADV_JOINTS[side+'Hand'+finger+str(segment)] = finger+'Finger'+str(segment)+'_'+suffix


def resolve_name(name, candidates):
    candidates = list(candidates)
    if name in candidates:
        return name
    # A fully specified namespace must match, rather than silently changing characters.
    specified = name.rsplit('|',1)[-1]
    if ':' in specified:
        hits = [n for n in candidates if n.rsplit('|',1)[-1] == specified]
    else:
        hits = [n for n in candidates if leaf_name(n) == name]
    if len(hits) != 1:
        raise ValueError(('Ambiguous node: ' if hits else 'Node does not exist: ')+name)
    return hits[0]


def skeleton_signature(names):
    import hashlib
    return hashlib.sha256('\n'.join(sorted(names)).encode('utf-8')).hexdigest()


def _auto(source_bones, target_bones, aliases, insensitive=False, identity=True):
    target_bones = list(target_bones)
    bm = BoneMap.from_mixamo_names(source_bones)
    bm.target_signature = skeleton_signature(target_bones)
    for row in bm.entries.values():
        for candidate in ((row.mixamo, aliases.get(row.mixamo)) if identity else (aliases.get(row.mixamo),)):
            if not candidate:
                continue
            matches = [n for n in target_bones if (leaf_name(n).lower() == candidate.lower() if insensitive else leaf_name(n) == candidate)]
            if len(matches) == 1:
                bm.set_target(row.mixamo, matches[0]); break
            if len(matches) > 1:
                raise ValueError('Multiple characters contain '+candidate+'; choose a unique target.')
    return bm


def auto_match_adv(source_bones, target_bones):
    return _auto(source_bones, target_bones, ADV_JOINTS)


def auto_match_controllers(source_bones, target_bones):
    from .adv_scan import MIXAMO_TO_ADV_FK
    target_bones=list(target_bones)
    aliases = dict(MIXAMO_TO_ADV_FK)
    for source, joint in ADV_JOINTS.items():
        aliases.setdefault(source, 'FK'+joint)
    result = _auto(source_bones, target_bones, aliases, identity=False)
    # Older/custom rigs without a whole-pelvis control may still expose FKRoot.
    # Do not choose it on ADV rigs that provide RootX_M.
    if 'Hips' in result.entries and not result.entries['Hips'].is_active():
        candidates = [n for n in target_bones if leaf_name(n) == 'FKRoot_M']
        if len(candidates) == 1:
            result.set_target('Hips', candidates[0])
    return result


def upgrade_adv_root_mapping(mapping, target_nodes):
    """Migrate legacy pelvis mappings only within the resolved character namespace."""
    mapping=dict(mapping);target_nodes=list(target_nodes);changed=False
    for role,name in list(mapping.items()):
        if leaf_name(role)!='Hips' or leaf_name(name)!='FKRoot_M':continue
        qualified=resolve_name(name,target_nodes).rsplit('|',1)[-1]
        replacement=qualified[:-len('FKRoot_M')]+'RootX_M'
        matches=[n for n in target_nodes if n.rsplit('|',1)[-1]==replacement]
        if len(matches)>1:raise ValueError('整体骨盆控制器名称不唯一：'+replacement)
        if matches:mapping[role]=matches[0];changed=True
    return mapping,changed


def auto_match_by_name(source_bones, target_bones):
    return _auto(source_bones, target_bones, {}, insensitive=True)


def verify_map(bm, target_bones):
    target_bones = list(target_bones)
    issues=[]
    for row in bm.entries.values():
        if not row.is_active():
            continue
        try:
            row.target = resolve_name(row.target, target_bones)
        except ValueError as exc:
            issues.append(str(exc))
    return issues


def duplicate_targets(bm: BoneMap) -> List[str]:
    """Return target names that are mapped from more than one mixamo bone.

    Drives silently overwrite each other; the user should resolve them
    or the last writer wins.
    """
    seen: Dict[str, str] = {}
    dups: List[str] = []
    for row in bm.entries.values():
        if not row.is_active():
            continue
        prev = seen.get(row.target)
        if prev is not None and prev != row.mixamo:
            dups.append(row.target)
        seen[row.target] = row.mixamo
    return sorted(set(dups))


# ---------------------------------------------------------------------------
# Utility for the UI
# ---------------------------------------------------------------------------

def sorted_bones(bm: BoneMap) -> List[MixamoTarget]:
    """Return entries sorted with the canonical mixamo order first, the
    rest alphabetically.  Gives the user a stable UI list."""
    order = {n: i for i, n in enumerate(MIXAMO_CANONICAL)}
    items = list(bm.entries.values())
    items.sort(key=lambda r: (order.get(r.mixamo, 10_000), r.mixamo.lower()))
    return items


def diff_from_default(bm: BoneMap,
                      default: BoneMap) -> Dict[str, str]:
    """Return only entries that differ from a default map (used by the
    UI to show 'changed' rows)."""
    out: Dict[str, str] = {}
    for k, row in bm.entries.items():
        d = default.entries.get(k)
        if d is None or d.target != row.target or d.enabled != row.enabled:
            out[k] = row.target
    return out
