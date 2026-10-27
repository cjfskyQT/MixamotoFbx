"""Continuously preserve source foot trajectories with a two-bone FK pose solve."""
import math


def source_knee_pole(om, hip, knee, ankle, heading, target_axis, previous=None):
    """Project the source bend onto the target leg's plane, retaining its side.

    Choosing the current target knee as a pole can preserve an already mirrored
    retargeted leg forever. Source geometry decides the bend hemisphere; use a
    transported previous direction only when the source leg is truly straight.
    """
    source_axis=ankle-hip
    source_upper=knee-hip
    source_bend=source_upper-source_axis*(source_upper*source_axis/max(source_axis*source_axis,1e-12))
    axis=target_axis.normal()
    pole=source_bend*heading
    pole-=axis*(pole*axis)
    if source_bend.length()>max(source_axis.length()*1e-6,1e-7) and pole.length()>1e-7:
        return pole.normal()
    if previous is not None:
        pole=previous-axis*(previous*axis)
        if pole.length()>1e-7:return pole.normal()
    pole=om.MVector(0,0,1)-axis*axis.z
    if pole.length()<1e-7:pole=om.MVector(1,0,0)-axis*axis.x
    return pole.normal()


def estimate_contact_floor(samples,marker_floor,height):
    observed=sorted(p[1] for p in samples)[max(0,int(len(samples)*.05)-1)]
    return max(marker_floor,min(observed,marker_floor+height*.08))


def stable_shape_offsets(offsets, phases, fps, contact_vectors=None):
    """Hold only morphology compensation in support; retain ALL source motion.

    On release, smoothly return to the rotating pelvis-relative compensation.
    Neither a source foot position nor a new contact position is ever anchored.
    """
    support={i:p.get('marker','toe') for p in phases for i in range(p['start'],p['end']+1)}
    output=[];previous_marker=None;release=None;anchor=None
    ramp=max(1,round(fps*.12))
    for i,desired in enumerate(offsets):
        if i in support:
            marker=support[i]
            vector=contact_vectors[marker][i] if contact_vectors else desired*0.
            previous_vector=contact_vectors[marker][max(0,i-1)] if contact_vectors else desired*0.
            if marker!=previous_marker:anchor=(output[-1]-previous_vector) if output else desired-vector
            value=anchor+vector;release=None
        else:
            if previous_marker is not None:release=(i-1,output[-1])
            if release is None:value=desired
            else:
                start,base=release
                u=min(1.,(i-start)/ramp);u=u*u*(3.-2.*u)
                value=base*(1.-u)+desired*u
        output.append(value);previous_marker=support.get(i)
    return output


def trajectory_goal(om, calibration, role, source_point, root_delta, offset_override=None):
    """Scale source motion; rotate the target's morphology offset with the pelvis.

    Equal-proportion rigs reduce exactly to a scaled source world trajectory.
    There are no contact reanchors, so genuine source foot travel is retained.
    """
    heading, scale = calibration['heading'], calibration['scale']
    source_bind = calibration['source_positions']
    target_bind = calibration['target_positions']
    origin = calibration['target_root_reference']
    source_origin = calibration['source_root_reference']
    shape_offset = target_bind[role] - origin - (source_bind[role]-source_origin)*heading*scale
    offset=shape_offset*root_delta if offset_override is None else offset_override
    return origin + (source_point-source_origin)*heading*scale + offset


def contact_phases(points, floor, height, fps):
    """Use both height and velocity; a fast foot skimming the floor is not planted."""
    height=max(height,1e-6)
    speeds=[]
    for i,p in enumerate(points):
        speeds.append(max([math.dist(p,points[j])*fps/height for j in (i-1,i+1) if 0<=j<len(points)] or [0.]))
    phases=[];start=None
    for i,p in enumerate(points):
        low=abs(p[1]-floor)/height
        if start is not None and (low>.04 or speeds[i]>.8):
            if i-start>=4:phases.append((start,i-1))
            start=None
        if start is None and low<.02 and speeds[i]<.36:
            start=i
    if start is not None and len(points)-start>=4:phases.append((start,len(points)-1))
    result=[]
    for start,end in phases:
        ramp=min(max(1,round(fps*.08)),(end-start)//2)
        weights={}
        for i in range(start,end+1):
            u=1.
            if start>0:u=min(u,(i-start+1)/(ramp+1))
            if end<len(points)-1:u=min(u,(end-i+1)/(ramp+1))
            weights[i]=u*u*(3.-2.*u)
        result.append(dict(start=start,end=end,weights=weights))
    return result


def adaptive_contact_phases(points,marker_reference_y,ground_offsets,height,fps):
    """Recover low support with a drifting reconstructed ground plane.

    A stable sample seeds a support run; the looser exit test extends that run
    both forwards and backwards. A global height guard excludes raised feet.
    Source positions are never frozen by this classification.
    """
    height=max(height,1e-6)
    eligible=[];seeds=[]
    for i,p in enumerate(points):
        speed=max([math.dist(p,points[j])*fps/height for j in (i-1,i+1) if 0<=j<len(points)] or [0.])
        clearance=p[1]-marker_reference_y-ground_offsets[i]
        near_reference=p[1]-marker_reference_y<height*.15
        eligible.append(near_reference and clearance<height*.04 and speed<.8)
        seeds.append(near_reference and clearance<height*.02 and speed<.36)
    phases=[];start=0
    while start<len(points):
        if not eligible[start]:start+=1;continue
        end=start
        while end+1<len(points) and eligible[end+1]:end+=1
        if end-start+1>=4 and any(seeds[start:end+1]):
            phases.append(dict(start=start,end=end,weights={i:1. for i in range(start,end+1)}))
        start=end+1
    return phases


def solve_contacts(cmds,om,calibration,pairs,reference_map,helpers,first,last,fps):
    source={s.rsplit('|',1)[-1].rsplit(':',1)[-1]:s for s in reference_map}
    driven={s.rsplit('|',1)[-1].rsplit(':',1)[-1]:t for s,t in pairs}
    anatomy=calibration['target_anatomy']
    floor=calibration['diagnostics']['source_ground']
    stats=dict(enabled=True,method='continuous_foot_trajectory_fk_solve',frames_adjusted=0,maximum_adjustment_cm=0.,
               knee_direction_policy='source_bend_plane',
               shape_offset_policy='pelvis_relative_with_source_motion_preserving_support',
               maximum_contact_trajectory_error_cm=0.,trajectory_samples=0,locked_samples=0,phases={},loop_seam_locked={},
               toe_clearance_samples=0,maximum_toe_clearance_adjustment_degrees=0.,maximum_extra_toe_penetration_cm=0.)
    if 'Hips' not in driven or floor is None:return stats
    height=calibration['source_root_reference'].y-floor
    legs={}
    calibration['foot_shape_offsets']={}
    calibration['foot_trajectory_targets']={}
    point=lambda n:om.MVector(cmds.xform(n,q=True,ws=True,t=True))
    rotation=lambda n:om.MTransformationMatrix(om.MMatrix(cmds.xform(n,q=True,ws=True,matrix=True))).rotation(asQuaternion=True).asMatrix()
    def key_rotation(helper,matrix,frame):
        e=om.MTransformationMatrix(matrix).rotation()
        cmds.setAttr(helper+'.rotate',*[math.degrees(v) for v in (e.x,e.y,e.z)],type='double3')
        cmds.setKeyframe(helper,attribute=['rx','ry','rz'],time=frame,inTangentType='linear',outTangentType='linear')
    for side in ('Left','Right'):
        roles=[side+r for r in ('UpLeg','Leg','Foot','ToeBase')]
        if not all(r in source and r in driven and r in anatomy for r in roles):continue
        samples=[];ankle_samples=[];shape_samples=[];ankle_vectors=[];ground_offsets=[]
        heading=calibration['heading'];scale=calibration['scale']
        toe=side+'ToeBase'
        shape=calibration['target_positions'][toe]-calibration['target_root_reference']-(calibration['source_positions'][toe]-calibration['source_root_reference'])*heading*scale
        for frame in range(first,last+1):
            cmds.currentTime(frame,edit=True,update=True)
            samples.append(list(point(source[side+'ToeBase'])))
            ankle_samples.append(list(point(source[side+'Foot'])))
            ground_offsets.append(min(point(source[r]).y-calibration['source_positions'][r].y for r in ('LeftFoot','LeftToeBase','RightFoot','RightToeBase') if r in source and r in calibration['source_positions']))
            ankle_vectors.append((point(source[side+'Foot'])-point(source[toe]))*heading*scale+point(anatomy[toe])-point(anatomy[side+'Foot']))
            delta=heading.inverse()*calibration['source_rest'][source['Hips']].inverse()*rotation(source['Hips'])*heading
            shape_samples.append(shape*delta)
        # Reconstructed motions may carry a small vertical ground offset.
        # Estimate the observed marker contact level, but never reinterpret a
        # foot far above its bind floor (e.g. a sustained kick) as grounded.
        marker_floor=calibration['source_positions'][toe].y
        contact_floor=estimate_contact_floor(samples,marker_floor,height)
        phases=contact_phases(samples,contact_floor,height,fps)
        ankle_floor=estimate_contact_floor(ankle_samples,calibration['source_positions'][side+'Foot'].y,height)
        ankle_phases=contact_phases(ankle_samples,ankle_floor,height,fps)
        recovered_toe=adaptive_contact_phases(samples,marker_floor,ground_offsets,height,fps)
        recovered_ankle=adaptive_contact_phases(ankle_samples,calibration['source_positions'][side+'Foot'].y,ground_offsets,height,fps)
        stats.setdefault('recovered_support',{})[side]={'toe':[(p['start']+first,p['end']+first) for p in recovered_toe],'ankle':[(p['start']+first,p['end']+first) for p in recovered_ankle]}
        phases+=recovered_toe;ankle_phases+=recovered_ankle
        toe_active={i for p in phases for i in range(p['start'],p['end']+1)}
        ankle_active={i for p in ankle_phases for i in range(p['start'],p['end']+1)}
        selected=[];previous_marker=None
        def speed(points,i):
            return max([math.dist(points[i],points[j]) for j in (i-1,i+1) if 0<=j<len(points)] or [0.])
        for i in range(len(samples)):
            candidates={}
            if i in toe_active:candidates['toe']=speed(samples,i)
            if i in ankle_active:candidates['ankle']=speed(ankle_samples,i)
            # A support point may genuinely translate/pivot in the source.
            # Speed alone must not trigger a morphology reset while it remains
            # low: release only after a clear lift above the local sole plane.
            if not candidates and previous_marker is not None:
                points=ankle_samples if previous_marker=='ankle' else samples
                reference=calibration['source_positions'][side+'Foot' if previous_marker=='ankle' else toe].y
                altitude=points[i][1]-reference
                if altitude-ground_offsets[i]<height*.04 and altitude<height*.15:
                    candidates[previous_marker]=speed(points,i)
            marker=min(candidates,key=candidates.get) if candidates else None
            if previous_marker in candidates and candidates[previous_marker]<=candidates[marker]*1.15+.02:marker=previous_marker
            selected.append(marker);previous_marker=marker
        phases=[]
        for i,marker in enumerate(selected):
            if marker is None:continue
            if i==0 or selected[i-1]!=marker:
                phases.append(dict(start=i,end=i,marker=marker,weights={i:1.}))
            else:phases[-1]['end']=i;phases[-1]['weights'][i]=1.
        calibration['foot_shape_offsets'][toe]=stable_shape_offsets(shape_samples,phases,fps,{'toe':[p*0. for p in shape_samples],'ankle':ankle_vectors})
        wraps=bool(phases and phases[0]['start']==0 and phases[-1]['end']==last-first and math.dist(samples[0],samples[-1])<height*.01)
        legs[side]=dict(roles=roles,phases=phases,selected=selected,ankle_vectors=ankle_vectors)
        stats.setdefault('observed_contact_floors',{})[side]={'toe':contact_floor,'ankle':ankle_floor}
        stats['loop_seam_locked'][side]=wraps
        stats['phases'][side]=[dict(start=p['start']+first,end=p['end']+first,
                                   marker=p.get('marker','toe'),
                                   locked_start=next((i+first for i,w in p['weights'].items() if w>.999999),None),
                                   locked_end=next((i+first for i,w in reversed(list(p['weights'].items())) if w>.999999),None)) for p in phases]
    root_helper=helpers[driven['Hips']]
    stats['solve_step_frames']=.25
    for sample in range((last-first)*4+1):
        frame=first+sample/4.
        cmds.currentTime(frame,edit=True,update=True)
        offset_index=frame-first;index=int(math.ceil(offset_index-1e-8));lower=int(math.floor(offset_index));fraction=offset_index-lower;active=[]
        nominal=point(root_helper)
        heading=calibration['heading']
        root_delta=heading.inverse()*calibration['source_rest'][source['Hips']].inverse()*rotation(source['Hips'])*heading
        for side,leg in legs.items():
            hip,knee,ankle,toe=leg['roles']
            a,b,c,p=[point(anatomy[r]) for r in leg['roles']]
            # Always retain the continuous source trajectory. Only the additive
            # morphology offset responds to turning/support; source positions
            # are never reanchored or replaced with a stationary foot position.
            offsets=calibration['foot_shape_offsets'][toe]
            morphology=offsets[index]
            if fraction>1e-8:
                marker=leg['selected'][index]
                if marker=='ankle':
                    current_vector=(point(source[ankle])-point(source[toe]))*heading*calibration['scale']+p-c
                    morphology=offsets[lower]-leg['ankle_vectors'][lower]+current_vector
                elif marker=='toe':morphology=offsets[lower]
                else:morphology=offsets[lower]*(1.-fraction)+offsets[index]*fraction
            toe_goal=trajectory_goal(om,calibration,toe,point(source[toe]),root_delta,morphology)
            calibration['foot_trajectory_targets'].setdefault(frame,{})[toe]=toe_goal
            active.append(dict(side=side,roles=leg['roles'],a=a,b=b,c=c,
                               goal=toe_goal-(p-c),toe_goal=toe_goal,anchor=toe_goal,
                               l1=(b-a).length(),l2=(c-b).length()))
        # Project root onto the reachable leg spheres only when necessary. This
        # never pulls the body towards whichever foot currently has a higher weight.
        # With both trajectories evaluated continuously there is no contact-release
        # offset to fade. A deterministic projection also preserves loop closure.
        offset=om.MVector()
        for _ in range(12):
            changed=False
            for leg in active:
                delta=leg['goal']-(leg['a']+offset);distance=delta.length()
                maximum=(leg['l1']+leg['l2'])*.9999
                minimum=abs(leg['l1']-leg['l2'])+1e-5
                if distance>maximum:
                    offset+=delta.normal()*(distance-maximum);changed=True
                elif distance<minimum and distance>1e-8:
                    offset-=delta.normal()*(minimum-distance);changed=True
            if not changed:break
        if offset.length()<1e-6:offset=om.MVector()
        cmds.xform(root_helper,ws=True,translation=list(nominal+offset))
        cmds.setKeyframe(root_helper,attribute=['tx','ty','tz'],time=frame,inTangentType='linear',outTangentType='linear')
        if frame==int(frame) and (active or offset.length()>1e-6):stats['frames_adjusted']+=1
        stats['maximum_adjustment_cm']=max(stats['maximum_adjustment_cm'],offset.length())
        for leg in active:
            hip,knee,ankle,toe=leg['roles']
            # Two world-space aim corrections solve the chain, while the ankle's
            # original world rotation stays on its independent FK helper.
            for _ in range(2):
                a,b,c=[point(anatomy[r]) for r in (hip,knee,ankle)]
                d=leg['goal']-a;distance=d.length()
                if distance<1e-8:continue
                axis=d.normal()
                state=legs[leg['side']]
                pole=source_knee_pole(om,*[point(source[r]) for r in (hip,knee,ankle)],
                                      heading,axis,state.get('pole_direction'))
                state['pole_direction']=pole
                x=(leg['l1']**2-leg['l2']**2+distance**2)/(2*distance)
                bend=math.sqrt(max(0.,leg['l1']**2-x*x))
                desired_knee=a+axis*x+pole.normal()*bend
                q=(b-a).rotateTo(desired_knee-a)
                key_rotation(helpers[driven[hip]],rotation(helpers[driven[hip]])*q.asMatrix(),frame)
                b,c=[point(anatomy[r]) for r in (knee,ankle)]
                q=(c-b).rotateTo(leg['goal']-b)
                key_rotation(helpers[driven[knee]],rotation(helpers[driven[knee]])*q.asMatrix(),frame)
            error=(point(anatomy[toe])-leg['anchor']).length()
            stats['maximum_contact_trajectory_error_cm']=max(stats['maximum_contact_trajectory_error_cm'],error)
            stats['trajectory_samples']+=1
            phase=next((p for p in legs[leg['side']]['phases'] if p['start']<=index<=p['end']),None)
            if phase and phase['weights'][index]>.999999:stats['locked_samples']+=1
            end=leg['side']+'Toe_End'
            if end in source and end in anatomy:
                # A longer target toe can enter the floor even with the correct
                # source rotation delta. Apply only the minimum necessary lift;
                # keep any original source penetration rather than invent motion.
                minimum=calibration['target_positions'][end].y+min(0.,(point(source[end]).y-calibration['source_positions'][end].y)*calibration['scale'])
                base,tip=point(anatomy[toe]),point(anatomy[end])
                if tip.y<minimum-1e-6:
                    vector=tip-base;length=vector.length()
                    horizontal=om.MVector(vector.x,0.,vector.z)
                    if length>1e-8 and horizontal.length()>1e-8:
                        dy=max(-length,min(length,minimum-base.y))
                        desired=horizontal.normal()*math.sqrt(max(0.,length*length-dy*dy))+om.MVector(0,dy,0)
                        q=vector.rotateTo(desired)
                        key_rotation(helpers[driven[toe]],rotation(helpers[driven[toe]])*q.asMatrix(),frame)
                        stats['toe_clearance_samples']+=1
                        stats['maximum_toe_clearance_adjustment_degrees']=max(stats['maximum_toe_clearance_adjustment_degrees'],math.degrees(2*math.acos(min(1.,abs(q.w)))))
                stats['maximum_extra_toe_penetration_cm']=max(stats['maximum_extra_toe_penetration_cm'],minimum-point(anatomy[end]).y)
    if stats['maximum_contact_trajectory_error_cm']>.05:
        raise RuntimeError('源脚部轨迹 FK 求解未达到精度，最大误差 %.4f 厘米。'%stats['maximum_contact_trajectory_error_cm'])
    if stats['maximum_extra_toe_penetration_cm']>.05:
        raise RuntimeError('脚趾末端超出参考地面约束 %.4f 厘米。'%stats['maximum_extra_toe_penetration_cm'])
    return stats
