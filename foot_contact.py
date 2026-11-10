"""Fit independent anatomical contacts, then solve editable leg motion."""
import math

CONTACT_SAMPLE_STEP=.125
SUPPORT_VALIDATE_STEP=CONTACT_SAMPLE_STEP/2.


def source_knee_pole(om,hip,knee,ankle,heading,target_axis,previous=None):
    source_axis=ankle-hip;source_upper=knee-hip
    source_bend=source_upper-source_axis*(source_upper*source_axis/max(source_axis*source_axis,1e-12))
    axis=target_axis.normal();pole=source_bend*heading;pole-=axis*(pole*axis)
    if source_bend.length()>max(source_axis.length()*1e-6,1e-7) and pole.length()>1e-7:return pole.normal()
    if previous is not None:
        pole=previous-axis*(previous*axis)
        if pole.length()>1e-7:return pole.normal()
    pole=om.MVector(0,0,1)-axis*axis.z
    if pole.length()<1e-7:pole=om.MVector(1,0,0)-axis*axis.x
    return pole.normal()


def validate_support_motion(cmds,om,calibration,phases,step=SUPPORT_VALIDATE_STEP,tolerance=.1):
    """Check absolute fitted anchors, source travel, and body motion after baking."""
    point=lambda n:om.MVector(cmds.xform(n,q=True,ws=True,t=True))
    c=calibration;sources=c['source_nodes'];targets=c['target_anatomy']
    maximum=0.;worst=None;count=0
    for side,runs in phases.items():
        for run in runs:
            role=side+('Foot' if run['marker']=='ankle' else 'ToeBase');anchor=om.MVector(run['anchor'])
            for sample in range(round((run['end']-run['start'])/step)+1):
                frame=run['start']+sample*step;cmds.currentTime(frame,edit=True,update=True)
                expected=point(sources[role])*c['heading']*c['scale']+anchor
                error=(point(targets[role])-expected).length();count+=1
                if error>maximum:maximum=error;worst=dict(role=role,frame=frame)
    if maximum>tolerance:raise RuntimeError('实际脚部偏离拟合支撑锚点 %.4f 厘米：%s 第 %g 帧。'%(maximum,worst['role'],worst['frame']))
    root_error=0.;plan=c['contact_plan'];first,last=c['contact_output_range']
    for i in range(round((last-first)/step)+1):
        frame=first+i*step;cmds.currentTime(frame,edit=True,update=True)
        index=(frame-plan['first'])*plan['divisions'];lo=int(math.floor(index+1e-8));hi=min(lo+1,len(plan['rows'])-1);u=index-lo
        offset=plan['rows'][lo]['root_offset']*(1-u)+plan['rows'][hi]['root_offset']*u
        expected=c['target_root_reference']+(point(sources['Hips'])-c['source_root_reference'])*c['heading']*c['scale']+om.MVector(0,offset,0)
        root_error=max(root_error,(point(c['contact_root_control'])-expected).length())
    if root_error>.05:raise RuntimeError('身体轨迹偏离源运动与有限垂直补偿 %.4f 厘米。'%root_error)
    return dict(maximum_drift_cm=maximum,worst=worst,samples=count,step_frames=step,tolerance_cm=tolerance,
                maximum_body_trajectory_error_cm=root_error,comparison='absolute_anatomical_anchor_not_first_output_sample')


def solve_contacts(cmds,om,calibration,pairs,reference_map,helpers,first,last,fps):
    from .contact_plan import build_plan
    c=calibration;source=c['source_nodes'];anatomy=c['target_anatomy']
    driven={s.rsplit('|',1)[-1].rsplit(':',1)[-1]:t for s,t in pairs}
    if 'Hips' not in driven or c['diagnostics']['source_ground'] is None:return dict(enabled=False,frames_adjusted=0,maximum_adjustment_cm=0.)
    c['contact_sides']=[s for s in ('Left','Right') if all(s+r in driven and s+r in anatomy and s+r in source for r in ('UpLeg','Leg','Foot','ToeBase'))]
    if not c['contact_sides']:return dict(enabled=False,frames_adjusted=0,maximum_adjustment_cm=0.)
    c['contact_root_control']=driven['Hips']
    plan=build_plan(cmds,om,c,CONTACT_SAMPLE_STEP,fps)
    c['contact_plan']=plan;c['contact_output_range']=(first,last);c['contact_root_control']=driven['Hips']
    stats=dict(plan['stats']);stats.update(enabled=True,knee_direction_policy='source_bend_plane',
        shape_offset_policy='anatomical_episode_fit_without_inherited_offset',frames_adjusted=0,
        maximum_contact_trajectory_error_cm=0.,trajectory_samples=0,locked_samples=0,toe_clearance_samples=0,
        maximum_toe_clearance_adjustment_degrees=0.,maximum_extra_toe_penetration_cm=0.,loop_seam_locked={})
    stats['phases']={s:[dict(p,start=max(first,p['start']),end=min(last,p['end'])) for p in ps if p['end']>=first and p['start']<=last] for s,ps in plan['stats']['phases'].items()}
    point=lambda n:om.MVector(cmds.xform(n,q=True,ws=True,t=True))
    rotation=lambda n:om.MTransformationMatrix(om.MMatrix(cmds.xform(n,q=True,ws=True,matrix=True))).rotation(asQuaternion=True).asMatrix()
    def key_rotation(helper,matrix,frame):
        e=om.MTransformationMatrix(matrix).rotation()
        cmds.setAttr(helper+'.rotate',*[math.degrees(v) for v in (e.x,e.y,e.z)],type='double3')
        cmds.setKeyframe(helper,attribute=['rx','ry','rz'],time=frame,inTangentType='linear',outTangentType='linear')
    root_helper=helpers[driven['Hips']];c['foot_trajectory_targets']={};previous_poles={}
    for sample in range((last-first)*plan['divisions']+1):
        frame=first+sample/plan['divisions'];cmds.currentTime(frame,edit=True,update=True)
        row=plan['rows'][round((frame-plan['first'])*plan['divisions'])]
        cmds.xform(root_helper,ws=True,translation=list(row['root']+om.MVector(0,row['root_offset'],0)))
        cmds.setKeyframe(root_helper,attribute=['tx','ty','tz'],time=frame,inTangentType='linear',outTangentType='linear')
        if abs(row['root_offset'])>1e-6 and frame==int(frame):stats['frames_adjusted']+=1
        for side,leg in row['legs'].items():
            hip,knee,ankle,toe=[side+r for r in ('UpLeg','Leg','Foot','ToeBase')];l1,l2=plan['lengths'][side]
            if (point(anatomy[hip])-(leg['hip']+om.MVector(0,row['root_offset'],0))).length()>.05:
                raise RuntimeError('骨盆参考与实际绑定不一致：%s 第 %g 帧。'%(side,frame))
            for _ in range(2):
                a,b,d=[point(anatomy[r]) for r in (hip,knee,ankle)];vector=leg['goal']-a;distance=vector.length()
                if distance<abs(l1-l2)+1e-5 or distance>l1+l2+1e-4:raise RuntimeError('规划脚位超出实际腿长：%s 第 %g 帧。'%(side,frame))
                axis=vector.normal();pole=source_knee_pole(om,*[point(source[r]) for r in (hip,knee,ankle)],c['heading'],axis,previous_poles.get(side));previous_poles[side]=pole
                x=(l1*l1-l2*l2+distance*distance)/(2*distance)
                desired=a+axis*x+pole*math.sqrt(max(0.,l1*l1-x*x));q=(b-a).rotateTo(desired-a)
                key_rotation(helpers[driven[hip]],rotation(helpers[driven[hip]])*q.asMatrix(),frame)
                b,d=[point(anatomy[r]) for r in (knee,ankle)];q=(d-b).rotateTo(leg['goal']-b)
                key_rotation(helpers[driven[knee]],rotation(helpers[driven[knee]])*q.asMatrix(),frame)
            error=(point(anatomy[toe])-leg['toe_goal']).length()
            stats['maximum_contact_trajectory_error_cm']=max(stats['maximum_contact_trajectory_error_cm'],error)
            c['foot_trajectory_targets'].setdefault(frame,{})[toe]=leg['toe_goal'];stats['trajectory_samples']+=1
            if any(p['start']<=frame<=p['end'] for p in stats['phases'][side]):stats['locked_samples']+=1
            end=side+'Toe_End'
            if end in source and end in anatomy:
                minimum=c['target_positions'][end].y+min(0.,(point(source[end]).y-c['source_positions'][end].y)*c['scale'])
                base,tip=point(anatomy[toe]),point(anatomy[end])
                if tip.y<minimum-1e-6:
                    vector=tip-base;length=vector.length();horizontal=om.MVector(vector.x,0,vector.z)
                    if length>1e-8 and horizontal.length()>1e-8:
                        dy=max(-length,min(length,minimum-base.y));desired=horizontal.normal()*math.sqrt(max(0.,length*length-dy*dy))+om.MVector(0,dy,0);q=vector.rotateTo(desired)
                        key_rotation(helpers[driven[toe]],rotation(helpers[driven[toe]])*q.asMatrix(),frame)
                        stats['toe_clearance_samples']+=1
                        stats['maximum_toe_clearance_adjustment_degrees']=max(stats['maximum_toe_clearance_adjustment_degrees'],math.degrees(2*math.acos(min(1.,abs(q.w)))))
                stats['maximum_extra_toe_penetration_cm']=max(stats['maximum_extra_toe_penetration_cm'],minimum-point(anatomy[end]).y)
    if stats['maximum_contact_trajectory_error_cm']>.05:raise RuntimeError('规划脚位求解误差 %.4f 厘米。'%stats['maximum_contact_trajectory_error_cm'])
    if stats['maximum_extra_toe_penetration_cm']>.05:raise RuntimeError('脚趾末端超出参考地面 %.4f 厘米。'%stats['maximum_extra_toe_penetration_cm'])
    return stats
