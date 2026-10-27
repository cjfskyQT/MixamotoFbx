"""Correct distributed ADV spine/neck controls using the driven joint positions."""
import math
from .bone_matcher import leaf_name


def solve_aims(cmds,om,calibration,pairs,helpers,first,last):
    driven={leaf_name(s):t for s,t in pairs}
    source={leaf_name(s):s for s,_ in pairs}
    anatomy=calibration['target_anatomy']
    children={'Spine':'Spine1','Spine1':'Spine2','Spine2':'Neck','Neck':'Head'}
    roles=[r for r,c in children.items() if r in driven and r in anatomy and c in anatomy and r in source and c in source]
    point=lambda n:om.MVector(cmds.xform(n,q=True,ws=True,t=True))
    rotation=lambda n:om.MTransformationMatrix(om.MMatrix(cmds.xform(n,q=True,ws=True,matrix=True))).rotation(asQuaternion=True).asMatrix()
    stats={'maximum_direction_error_degrees':0.,'roles':{},'frames':last-first+1}
    for frame in range(first,last+1):
        cmds.currentTime(frame,edit=True)
        desired={r:(point(source[children[r]])-point(source[r]))*calibration['heading'] for r in roles}
        for iteration in range(40):
            largest=0.
            for r in roles:
                actual=point(anatomy[children[r]])-point(anatomy[r])
                if min(actual.length(),desired[r].length())<1e-8:continue
                angle=actual.angle(desired[r]);largest=max(largest,math.degrees(angle))
                if angle<1e-5:continue
                correction=om.MQuaternion.slerp(om.MQuaternion(),actual.rotateTo(desired[r]),.5)
                helper=helpers[driven[r]]
                e=om.MTransformationMatrix(rotation(helper)*correction.asMatrix()).rotation()
                cmds.setAttr(helper+'.rotate',*[math.degrees(v) for v in (e.x,e.y,e.z)],type='double3')
            if largest<.01:break
        for r in roles:
            helper=helpers[driven[r]]
            cmds.setKeyframe(helper,attribute=['rx','ry','rz'],time=frame,inTangentType='linear',outTangentType='linear')
            actual=point(anatomy[children[r]])-point(anatomy[r])
            if min(actual.length(),desired[r].length())<1e-8:continue
            error=math.degrees(actual.angle(desired[r]))
            stats['roles'][r]=max(stats['roles'].get(r,0.),error)
            stats['maximum_direction_error_degrees']=max(stats['maximum_direction_error_degrees'],error)
    return stats
