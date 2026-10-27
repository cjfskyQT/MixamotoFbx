"""Order-aware Euler continuity for baked FK animation."""
import math


def unwrap_eulers(om,values,order):
    result=[];previous=None
    for value in values:
        current=om.MEulerRotation(*[math.radians(v) for v in value],order)
        if previous is not None:current=current.closestSolution(previous)
        result.append([math.degrees(v) for v in (current.x,current.y,current.z)])
        previous=current
    return result


def make_continuous(cmds,om,targets,first,last):
    """Keep sampled poses, unwrap using each control's actual rotation order."""
    for node in targets:
        times=sorted(set(cmds.keyframe(node,attribute=['rx','ry','rz'],time=(first,last),query=True,timeChange=True) or [first,last]))
        values=[cmds.getAttr(node+'.rotate',time=frame)[0] for frame in times]
        continuous=unwrap_eulers(om,values,cmds.getAttr(node+'.rotateOrder'))
        for frame,angles in zip(times,continuous):
            for axis,value in zip('xyz',angles):
                cmds.setKeyframe(node,attribute='r'+axis,time=frame,value=value,inTangentType='linear',outTangentType='linear')
        cmds.keyTangent(node,attribute=['rx','ry','rz'],time=(first,last),inTangentType='linear',outTangentType='linear')
