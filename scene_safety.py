"""Inspect scene text before any Maya file operation.

The signatures below are well-known fingerprints of Maya malware
samples that Autodesk has documented in their ScriptExploit support
article.  ADV / mGear / HumanIK character-rig exports do not emit any
of these strings, so a hit on any of them means the .ma has been
tampered with - either at save time by an already-running virus, or
because the source wasn't trusted.

The list is intentionally narrow (7 strings).  A wider list would
produce false positives on legitimate `MayaSecureSystem`-class API
names; a narrower one would miss new variants.  These 7 strings are
chosen so a positive match is unambiguous.
"""
from pathlib import Path

SIGNATURES = ('MayaSecureSystem', 'Embedded Bootstrap', 'maya_secure_system_scriptNode',
              'phage', 'fuckVirus', 'petri_dish', 'breed_gene')


def scan_text(text):
    return [s for s in SIGNATURES if s.lower() in text.lower()]


def validate_scene(path):
    path = Path(path)
    if path.suffix.lower() == '.ma':
        threats = scan_text(path.read_text(encoding='latin-1'))
        if threats:
            raise RuntimeError(
                'Refusing to load MA file with known-malware signatures:\n'
                f'  {path}\n'
                f'  matched: {", ".join(threats)}\n'
                '\n'
                'These fingerprints come from the Autodesk ScriptExploit\n'
                'family (https://www.autodesk.com/support/technical/article/\n'
                'caas/sfdcarticles/sfdcarticles/How-to-diagnose-and-clean-Maya-\n'
                'ScriptExploit-issues.html) and from the public "fuckVirus"/\n'
                '"phage" sample.  No legitimate character-binding plugin\n'
                '(Advanced Skeleton, mGear, HumanIK, BlenRig) emits any\n'
                'of them.  If this file came from a rig that *you* built\n'
                'and you are confident the scriptNode blocks are remnants\n'
                'of an earlier infection, clean them with\n'
                '`delete -all -scriptNode` in a quarantined Maya before\n'
                're-exporting.  Otherwise retarget this rig from an FBX\n'
                'export instead of the MA.')
    elif path.suffix.lower() != '.fbx':
        raise ValueError('Only FBX and Maya ASCII (.ma) files are accepted.')
