"""Calibrate and validate palms from corresponding finger bases, never a thumb fallback."""
import math
from .bone_matcher import leaf_name


def palm_frame(om,wrist,index,ring):
    aim=(index+ring)*.5-wrist
    across=index-ring
    if aim.length()<1e-7:return None
    aim.normalize();across-=aim*(across*aim)
    if across.length()<1e-7:return None
    across.normalize();normal=aim^across;normal.normalize()
    return om.MMatrix([aim.x,aim.y,aim.z,0,across.x,across.y,across.z,0,normal.x,normal.y,normal.z,0,0,0,0,1])


def bone_frame(om,aim,normal):
    """A full finger frame fixes the roll ambiguity of single-vector aiming."""
    if aim.length()<1e-7:return None
    aim=aim.normal();up=normal-aim*(normal*aim)
    if up.length()<1e-7:return None
    up.normalize();across=up^aim
    return om.MMatrix([aim.x,aim.y,aim.z,0,across.x,across.y,across.z,0,up.x,up.y,up.z,0,0,0,0,1])


def solve_hands(cmds,om,calibration,pairs,helpers,first,last):
    source=calibration['source_nodes']
    driven={leaf_name(s):t for s,t in pairs};anatomy=calibration['target_anatomy']
    point=lambda n:om.MVector(cmds.xform(n,q=True,ws=True,t=True))
    rot=lambda n:om.MTransformationMatrix(om.MMatrix(cmds.xform(n,q=True,ws=True,matrix=True))).rotation(asQuaternion=True).asMatrix()
    roles={}
    for side in ('Left','Right'):
        r=side+'Hand';markers=[r,r+'Index1',r+'Ring1']
        if r in driven and all(n in source and n in anatomy for n in markers):roles[r]=markers
    stats={'maximum_palm_frame_error_degrees':0.,'samples':0,'roles':list(roles)}
    for frame in range(first,last+1):
        cmds.currentTime(frame,edit=True)
        for role,markers in roles.items():
            desired=palm_frame(om,*[point(source[n]) for n in markers])
            if desired is None:continue
            desired=desired*calibration['heading']
            for _ in range(6):
                actual=palm_frame(om,*[point(anatomy[n]) for n in markers])
                if actual is None:break
                delta=actual.inverse()*desired
                q=om.MTransformationMatrix(delta).rotation(asQuaternion=True)
                error=math.degrees(2*math.acos(min(1.,abs(q.w))))
                if error<.001:break
                helper=helpers[driven[role]]
                e=om.MTransformationMatrix(rot(helper)*delta).rotation()
                cmds.setAttr(helper+'.rotate',*[math.degrees(v) for v in (e.x,e.y,e.z)],type='double3')
            cmds.setKeyframe(helpers[driven[role]],attribute=['rx','ry','rz'],time=frame,inTangentType='linear',outTangentType='linear')
            actual=palm_frame(om,*[point(anatomy[n]) for n in markers])
            if actual is not None:
                q=om.MTransformationMatrix(actual.inverse()*desired).rotation(asQuaternion=True)
                stats['maximum_palm_frame_error_degrees']=max(stats['maximum_palm_frame_error_degrees'],math.degrees(2*math.acos(min(1.,abs(q.w)))))
                stats['samples']+=1
    return stats
