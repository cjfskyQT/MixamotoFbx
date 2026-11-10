"""Offline, anatomy-referenced foot placement without inherited world offsets."""
import math


def smooth_bounded(values, lower, upper, strength):
    """Box-constrained least squares: displacement plus temporal velocity cost."""
    size=len(values)
    if size==1:return [max(lower[0],min(upper[0],0.))]
    penalty=math.sqrt(1.+4.*strength)
    denominators=[];factors=[]
    for i in range(size):
        diagonal=1.+penalty+strength*(1 if i in (0,size-1) else 2)
        denominator=diagonal+(strength*factors[-1] if i else 0.)
        denominators.append(denominator);factors.append(-strength/denominator)
    z=list(values);dual=[0.]*size
    for iteration in range(1200):
        forward=[]
        for i in range(size):
            rhs=penalty*(z[i]-dual[i])+(strength*forward[-1] if i else 0.)
            forward.append(rhs/denominators[i])
        x=list(forward)
        for i in range(size-2,-1,-1):x[i]-=factors[i]*x[i+1]
        primal=change=0.
        for i in range(size):
            value=max(lower[i],min(upper[i],x[i]+dual[i]))
            primal=max(primal,abs(x[i]-value));change=max(change,abs(value-z[i]))
            z[i]=value;dual[i]+=x[i]-value
        if primal<1e-5 and change<1e-5:return z
    raise RuntimeError('离线骨盆补偿未收敛，拒绝生成可能存在突变的动画。')


def support_runs(points, source_bind, height, fps):
    """Require observed low, slow support; the current lowest foot is not a floor."""
    count=len(points);window=max(1,round(fps*.5))
    minimum=[min(row[side][marker].y-source_bind[side][marker].y
                 for side in points[0] for marker in ('toe','ankle')) for row in points]
    ground=[]
    for i in range(count):
        local=sorted(minimum[max(0,i-window):min(count,i+window+1)])
        ground.append(local[int((len(local)-1)*.2)])
    rate=height*.03/fps
    for indices in (range(1,count),range(count-2,-1,-1)):
        for i in indices:
            j=i-1 if indices.step>0 else i+1
            ground[i]=max(ground[j]-rate,min(ground[j]+rate,ground[i]))
    runs={}
    for side in points[0]:
        eligible=[];seeds=[]
        for i in range(count):
            clearances=[];speeds=[]
            for marker in ('toe','ankle'):
                point=points[i][side][marker]
                speed=max((point-points[j][side][marker]).length()*fps
                          for j in (max(0,i-1),min(count-1,i+1)))
                clearance=point.y-source_bind[side][marker].y-ground[i]
                clearances.append(clearance);speeds.append(speed)
            # Height and pivot velocity describe the whole foot. During a roll,
            # the low toe and the nearly stationary ankle can be different markers.
            eligible.append(min(clearances)<height*.035 and min(speeds)<height*.4)
            seeds.append(min(clearances)<height*.02 and min(speeds)<height*.18)
        phases=[];i=0
        while i<count:
            if not eligible[i]:i+=1;continue
            start=i
            while i+1<count and eligible[i+1]:i+=1
            end=i;i+=1
            if end-start+1<max(4,round(fps*.12)):continue
            if not any(seeds[start:end+1]):continue
            marker=min(('toe','ankle'),key=lambda m:sum(
                (points[j][side][m]-points[j-1][side][m]).length() for j in range(start+1,end+1)))
            phases.append(dict(start=start,end=end,marker=marker))
        runs[side]=phases
    return runs,ground


def _hermite(a,b,va,vb,u,duration):
    return (a*(2*u**3-3*u*u+1)+va*((u**3-2*u*u+u)*duration)
            +b*(-2*u**3+3*u*u)+vb*((u**3-u*u)*duration))


def fill_flights(corrections, phases, divisions, fps, zero):
    """Plan contact entry/exit together; no release state passes into the next run."""
    if not phases:return [zero for _ in corrections]
    intervals=[];previous=None
    for phase in phases:
        start=phase['start']*divisions;end=phase['end']*divisions
        intervals.append((previous,start));previous=end
    intervals.append((previous,None))
    for left,right in intervals:
        lo=0 if left is None else left;hi=len(corrections)-1 if right is None else right
        if hi<=lo:continue
        a=zero if left is None else corrections[left]
        b=zero if right is None else corrections[right]
        va=zero if left is None or left==0 else (corrections[left]-corrections[left-1])*divisions
        vb=zero if right is None or right+1>=len(corrections) else (corrections[right+1]-corrections[right])*divisions
        duration=(hi-lo)/divisions
        window=min(fps*.2,duration/2.)
        for i in range(lo,hi+1):
            if corrections[i] is not None:continue
            t=(i-lo)/divisions
            if left is not None and right is not None and duration<=fps*.4:
                value=_hermite(a,b,va,vb,t/duration,duration)
            elif left is not None and t<window:
                value=_hermite(a,zero,va,zero,t/window,window)
            elif right is not None and duration-t<window:
                value=_hermite(zero,b,zero,vb,(t-duration+window)/window,window)
            else:value=zero
            corrections[i]=value
    return corrections


def build_plan(cmds,om,c,step,fps):
    source=c['source_nodes'];target=c['target_positions'];heading=c['heading'];scale=c['scale']
    times=cmds.keyframe(list(source.values()),q=True,timeChange=True) or [0.]
    first=int(math.floor(min(times)));last=int(math.ceil(max(times)));divisions=round(1./step)
    point=lambda n:om.MVector(cmds.xform(n,q=True,ws=True,t=True))
    rotation=lambda n:om.MTransformationMatrix(om.MMatrix(cmds.xform(n,q=True,ws=True,matrix=True))).rotation(asQuaternion=True).asMatrix()
    sides=[s for s in c.get('contact_sides',('Left','Right')) if all(s+r in source and s+r in target for r in ('UpLeg','Leg','Foot','ToeBase'))]
    lengths={s:((target[s+'Leg']-target[s+'UpLeg']).length(),(target[s+'Foot']-target[s+'Leg']).length()) for s in sides}
    rows=[]
    for sample in range((last-first)*divisions+1):
        frame=first+sample/divisions;cmds.currentTime(frame,edit=True,update=True)
        root=c['target_root_reference']+(point(source['Hips'])-c['source_root_reference'])*heading*scale
        root_delta=heading.inverse()*c['source_rest'][source['Hips']].inverse()*rotation(source['Hips'])*heading
        root_control=c.get('contact_root_control')
        if root_control:
            root_delta=c['target_control_reference'][root_control].inverse()*c['target_rest'][root_control]*root_delta
        row=dict(frame=frame,root=root,legs={})
        for side in sides:
            a,b,d,p=[point(source[side+r]) for r in ('UpLeg','Leg','Foot','ToeBase')]
            hip=root+(target[side+'UpLeg']-c['target_root_reference'])*root_delta
            l1,l2=lengths[side]
            ankle=hip+(b-a).normal()*heading*l1+(d-b).normal()*heading*l2
            foot_delta=heading.inverse()*c['source_rest'][source[side+'Foot']].inverse()*rotation(source[side+'Foot'])*heading
            toe_vector=(target[side+'ToeBase']-target[side+'Foot'])*foot_delta
            flex=math.degrees((b-a).angle(d-b))
            row['legs'][side]=dict(hip=hip,natural_ankle=ankle,natural_toe=ankle+toe_vector,
                                    source_ankle=d,source_toe=p,toe_vector=toe_vector,flex=flex)
        rows.append(row)
    integer=[{s:{m:r['legs'][s]['source_'+m] for m in ('ankle','toe')} for s in sides} for r in rows[::divisions]]
    source_bind={s:dict(ankle=c['source_positions'][s+'Foot'],toe=c['source_positions'][s+'ToeBase']) for s in sides}
    height=c['source_root_reference'].y-c['diagnostics']['source_ground']
    phases,ground=support_runs(integer,source_bind,height,fps)
    stats=dict(method='offline_anatomical_contacts_v2',ground_policy='windowed_lower_quantile_with_rate_limit',
               anchor_policy='independent_whole_episode_anatomical_fit',maximum_pose_adjustment_cm=0.,
               maximum_adjustment_cm=0.,maximum_horizontal_root_adjustment_cm=0.,phases={},solve_step_frames=step)
    for side in sides:
        corrections=[None]*len(rows)
        for phase in phases[side]:
            marker=phase['marker'];role=side+('Foot' if marker=='ankle' else 'ToeBase')
            lo=phase['start']*divisions;hi=phase['end']*divisions
            samples=[rows[i]['legs'][side]['natural_'+marker]-rows[i]['legs'][side]['source_'+marker]*heading*scale for i in range(lo,hi+1)]
            anchor=sum(samples,om.MVector())/len(samples)
            anchor.y=target[role].y-c['source_positions'][role].y*scale
            phase['anchor']=list(anchor)
            for i in range(lo,hi+1):
                leg=rows[i]['legs'][side]
                corrections[i]=leg['source_'+marker]*heading*scale+anchor-leg['natural_'+marker]
        fill_flights(corrections,phases[side],divisions,fps,om.MVector())
        for i,row in enumerate(rows):
            correction=corrections[i];leg=row['legs'][side]
            stats['maximum_pose_adjustment_cm']=max(stats['maximum_pose_adjustment_cm'],correction.length())
            if correction.length()>sum(lengths[side])*.35:
                raise RuntimeError('接触锚点与当前腿部几何冲突：%s 第 %g 帧，调整 %.3f 厘米。'%(side,row['frame'],correction.length()))
            leg['goal']=leg['natural_ankle']+correction
            leg['toe_goal']=leg['goal']+leg['toe_vector']
        stats['phases'][side]=[dict(start=p['start']+first,end=p['end']+first,marker=p['marker'],anchor=p['anchor']) for p in phases[side]]
    lower=[];upper=[];initial=[];bound=min(sum(v) for v in lengths.values())*.1
    for row in rows:
        low=-bound;high=bound
        for side,leg in row['legs'].items():
            l1,l2=lengths[side];flex=min(leg['flex'],max(2.,leg['flex']*.5))
            radius=math.sqrt(l1*l1+l2*l2+2*l1*l2*math.cos(math.radians(flex)))*.999999
            delta=leg['goal']-leg['hip'];square=radius*radius-delta.x*delta.x-delta.z*delta.z
            if square<0:raise RuntimeError('支撑锚点横向超出腿长：%s 第 %g 帧。'%(side,row['frame']))
            vertical=math.sqrt(square);low=max(low,delta.y-vertical);high=min(high,delta.y+vertical)
        if low>high:raise RuntimeError('支撑位置需要过大的骨盆补偿：第 %g 帧。'%row['frame'])
        lower.append(low);upper.append(high);initial.append(max(low,min(high,0.)))
    vertical=smooth_bounded(initial,lower,upper,(fps*.12/step)**2)
    for row,value in zip(rows,vertical):row['root_offset']=value
    stats['maximum_adjustment_cm']=max(abs(v) for v in vertical)
    stats['maximum_root_speed_cm_per_frame']=max([abs(b-a)/step for a,b in zip(vertical,vertical[1:])] or [0.])
    stats['root_adjustment_limit_cm']=bound
    return dict(first=first,last=last,divisions=divisions,rows=rows,lengths=lengths,stats=stats,ground=ground)
