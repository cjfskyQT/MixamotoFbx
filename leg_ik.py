"""Bake Advanced Skeleton FK legs onto the existing IK controls."""
import math


def bake_legs_to_ik(cmds, om, first, last, namespaces=('',), step=.125,
                    position_tolerance=.05, rotation_tolerance=.1, knee_tolerance=.3, knee_rotation_tolerance=.2):
    if step <= 0 or last < first:
        raise ValueError('Invalid IK bake range or sample interval.')
    times=[first+i*step for i in range(int(math.floor((last-first)/step))+1)]
    if times[-1] < last-1e-8: times.append(last)
    def world(node): return om.MMatrix(cmds.xform(node,q=True,ws=True,matrix=True))
    def point(matrix): return om.MVector(matrix[12],matrix[13],matrix[14])
    def rot(matrix): return om.MTransformationMatrix(matrix).rotation(asQuaternion=True)
    def resolve(name):
        matches=cmds.ls(name,long=True) or []
        if len(matches)!=1: raise RuntimeError('IK control/joint missing or ambiguous: '+name)
        return matches[0]
    legs=[]
    for ns in sorted(set(namespaces)):
        for side in ('L','R'):
            prefix=(ns+':' if ns else '')
            nodes={name:resolve(prefix+name+'_'+side) for name in (
                'Hip','Knee','Ankle','Toes','ToesEnd','IKLeg','PoleLeg','IKToes',
                'FKIKLeg','IKFKAlignedLeg','IKLegHandle','IKToesHandle','IKXKnee','FKXKnee')}
            plug=nodes['FKIKLeg']+'.FKIKBlend'
            maximum=cmds.attributeQuery('FKIKBlend',node=nodes['FKIKLeg'],maximum=True)
            if not maximum or abs(maximum[0]-10)>1e-6:
                raise RuntimeError('Unsupported FKIKBlend range: '+plug)
            for name,attrs in [('IKLeg',['tx','ty','tz','rx','ry','rz']),
                               ('PoleLeg',['tx','ty','tz']),('IKToes',['tx','ty','tz','rx','ry','rz']),
                               ('FKIKLeg',['FKIKBlend'])]:
                for attr in attrs:
                    plug=nodes[name]+'.'+attr
                    incoming=cmds.listConnections(plug,s=True,d=False) or []
                    if cmds.getAttr(plug,lock=True) or any(not cmds.nodeType(n).startswith('animCurve') for n in incoming):
                        raise RuntimeError('IK channel is locked or connected: '+plug)
            legs.append(dict(nodes=nodes,poses={},pole_direction=None))
    # Capture all poses before changing either switch. Include toe tips and knees.
    for frame in times:
        cmds.currentTime(frame,edit=True)
        for leg in legs:
            n=leg['nodes']
            if abs(cmds.getAttr(n['FKIKLeg']+'.FKIKBlend'))>1e-6:
                raise RuntimeError('Leg conversion requires FK animation throughout the requested range.')
            leg['poses'][frame]={name:world(n[name]) for name in ('Hip','Knee','Ankle','Toes','ToesEnd')}
    # Neutralize unused IK foot-roll/length overrides, retaining FK animation.
    for leg in legs:
        n=leg['nodes']
        # The source FK knee may contain axial twist that the two-bone IK
        # solver cannot represent. Preserve it on a child of the IK driver;
        # this child follows future IK edits while the FK branch is untouched.
        constraints=[]
        for axis in 'xyz':constraints.extend(cmds.listConnections(n['Knee']+'.r'+axis,s=True,d=False,type='orientConstraint') or [])
        constraints=list(set(constraints))
        if len(constraints)!=1:raise RuntimeError('Unsupported knee orientation driver: '+n['Knee'])
        constraint=constraints[0]
        indices=cmds.getAttr(constraint+'.target',multiIndices=True) or []
        index=None
        existing_helper=None
        name=n['IKLeg'].rsplit('|',1)[-1].rsplit(':',1)[-1].replace('IKLeg_','IKBakeKneeRotation_')
        for i in indices:
            incoming=cmds.listConnections('%s.target[%d].targetRotate'%(constraint,i),s=True,d=False) or []
            for source in incoming:
                full=(cmds.ls(source,long=True) or [None])[0]
                if full==n['IKXKnee']:
                    index=i
                elif (full and full.rsplit('|',1)[-1].rsplit(':',1)[-1]==name
                      and cmds.listRelatives(full,parent=True,fullPath=True)==[n['IKXKnee']]):
                    index=i
                    existing_helper=full
        if index is None:raise RuntimeError('No IK knee target on orientation constraint: '+constraint)
        ns_prefix=n['IKLeg'].rsplit('|',1)[-1].rpartition(':')[0]
        qualified=(ns_prefix+':' if ns_prefix else '')+name
        if existing_helper:
            # Reuse only our original FK/IK twist graph. A similarly named
            # controller or a helper belonging to another character is unsafe.
            def input_node(plug, expected_type):
                sources=list(set(cmds.listConnections(plug,s=True,d=False) or []))
                if len(sources)!=1 or cmds.nodeType(sources[0])!=expected_type:
                    raise RuntimeError('Unsupported existing knee correction: '+existing_helper)
                return sources[0]
            euler=input_node(existing_helper+'.rotateX','quatToEuler')
            normalize=input_node(euler+'.inputQuat','quatNormalize')
            decompose=input_node(normalize+'.inputQuatX','decomposeMatrix')
            if input_node(normalize+'.inputQuatW','decomposeMatrix')!=decompose:
                raise RuntimeError('Unsupported existing knee correction: '+existing_helper)
            relative=input_node(decompose+'.inputMatrix','multMatrix')
            for slot,expected in [(0,n['FKXKnee']),(1,n['IKXKnee'])]:
                sources=cmds.listConnections('%s.matrixIn[%d]'%(relative,slot),s=True,d=False) or []
                if len(sources)!=1 or (cmds.ls(sources[0],long=True) or [None])[0]!=expected:
                    raise RuntimeError('Knee correction references a different character: '+existing_helper)
            n['KneeRotation']=existing_helper
        else:
            if cmds.objExists(qualified) or cmds.objExists(n['IKXKnee']+'|'+name):raise RuntimeError('Knee correction already exists: '+qualified)
            helper=cmds.createNode('transform',name=name,parent=n['IKXKnee'])
            n['KneeRotation']=cmds.ls(helper,long=True)[0]
        # Extract only axial twist from the original FK frame, in IK space.
        # Swing remains driven by IK. Unlike a sampled Euler correction this
        # remains continuous when the solver changes branch at a straight knee.
        if not existing_helper:
            if not cmds.pluginInfo('quatNodes',q=True,loaded=True):cmds.loadPlugin('quatNodes',quiet=True)
            relative=cmds.createNode('multMatrix',name=name+'_relative')
            decompose=cmds.createNode('decomposeMatrix',name=name+'_decompose')
            normalize=cmds.createNode('quatNormalize',name=name+'_normalize')
            euler=cmds.createNode('quatToEuler',name=name+'_euler')
            cmds.connectAttr(n['FKXKnee']+'.worldMatrix[0]',relative+'.matrixIn[0]')
            cmds.connectAttr(n['IKXKnee']+'.worldInverseMatrix[0]',relative+'.matrixIn[1]')
            cmds.connectAttr(relative+'.matrixSum',decompose+'.inputMatrix')
            for axis in ('X','W'):cmds.connectAttr(decompose+'.outputQuat'+axis,normalize+'.inputQuat'+axis)
            cmds.connectAttr(normalize+'.outputQuat',euler+'.inputQuat')
            cmds.connectAttr(euler+'.outputRotateX',n['KneeRotation']+'.rotateX')
        for source_attr,target_attr in [('rotate','targetRotate'),('rotateOrder','targetRotateOrder'),('parentMatrix[0]','targetParentMatrix')]:
            cmds.connectAttr(n['KneeRotation']+'.'+source_attr,'%s.target[%d].%s'%(constraint,index,target_attr),force=True)
        joint_orient='%s.target[%d].targetJointOrient'%(constraint,index)
        for plug in cmds.listConnections(joint_orient,s=True,d=False,plugs=True) or []:cmds.disconnectAttr(plug,joint_orient)
        cmds.setAttr(joint_orient,0,0,0,type='double3')
        # Advanced Skeleton's deformation twist matrices use the same IK
        # orientation independently of the joint's orientConstraint.
        destinations=cmds.listConnections(n['IKXKnee']+'.worldMatrix[0]',s=False,d=True,plugs=True) or []
        for plug in destinations:
            node=plug.split('.',1)[0]
            if cmds.nodeType(node)=='blendMatrix' and node.rsplit(':',1)[-1].startswith('KneeBM_'):
                cmds.connectAttr(n['KneeRotation']+'.worldMatrix[0]',plug,force=True)
        for name,attrs in [('IKLeg',dict(roll=0,rock=0,swivel=0,antiPop=0,stretchy=0,Lenght1=1,Lenght2=1)),
                           ('PoleLeg',{'lock':0}),('RollHeel',dict(rx=0,ry=0,rz=0)),
                           ('RollToes',dict(rx=0,ry=0,rz=0)),('RollToesEnd',dict(rx=0,ry=0,rz=0))]:
            node=n.get(name)
            if node is None:
                candidate=n['IKLeg'].rsplit('|',1)[-1].replace('IKLeg_',name+'_')
                matches=cmds.ls(candidate,long=True) or []
                node=matches[0] if len(matches)==1 else None
            if not node: continue
            for attr,value in attrs.items():
                plug=node+'.'+attr
                if cmds.objExists(plug):
                    if cmds.getAttr(plug,lock=True): raise RuntimeError('IK option locked: '+plug)
                    cmds.cutKey(plug,time=(first,last),clear=True)
                    for frame in (first,last):cmds.setKeyframe(plug,time=frame,value=value,inTangentType='linear',outTangentType='step')
    # The FK poses above are now cached. Replace IK animation only inside the
    # requested interval; off-grid old keys would otherwise survive resampling
    # and create unvalidated excursions between the new bake samples.
    for leg in legs:
        n=leg['nodes']
        for name,attrs in [('IKLeg',['tx','ty','tz','rx','ry','rz']),
                           ('PoleLeg',['tx','ty','tz']),('IKToes',['tx','ty','tz','rx','ry','rz']),
                           ('FKIKLeg',['FKIKBlend'])]:
            cmds.cutKey(n[name],attribute=attrs,time=(first,last),clear=True)
    def match_frame(frame):
        for leg in legs:
            n=leg['nodes'];pose=leg['poses'][frame]
            ctrl=world(n['IKLeg'])
            offset=world(n['IKFKAlignedLeg'])*ctrl.inverse()
            desired=offset.inverse()*pose['Ankle']
            cmds.xform(n['IKLeg'],ws=True,matrix=list(desired))
            # Match the IK handle's endpoint after rotation, without relying on
            # a particular rig version's control pivot or foot-roll offsets.
            delta=point(pose['Ankle'])-point(world(n['IKLegHandle']))
            p=point(world(n['IKLeg']))+delta
            cmds.xform(n['IKLeg'],ws=True,t=(p.x,p.y,p.z))
            hip,knee,ankle=[point(pose[x]) for x in ('Hip','Knee','Ankle')]
            line=ankle-hip
            bend=knee-(hip+line*((knee-hip)*line/max(line*line,1e-12)))
            if bend.length()>1e-5:direction=bend.normal();leg['pole_direction']=direction
            else:
                direction=leg['pole_direction']
                if direction is None:
                    direction=point(world(n['PoleLeg']))-knee
                    direction-=line*(direction*line/max(line*line,1e-12))
                    if direction.length()<1e-6: raise RuntimeError('Cannot resolve pole direction for a straight leg.')
                    direction=direction.normal()
            # A distant, normalized pole flips abruptly when a knee crosses
            # straight. Keep its distance proportional to the actual bend so
            # the pole trajectory remains continuous across those poses.
            pole=knee+direction*((knee-hip).length()+(ankle-knee).length())
            cmds.xform(n['PoleLeg'],ws=True,t=(pole.x,pole.y,pole.z))
            cmds.xform(n['IKToes'],ws=True,matrix=list(pose['Toes']))
            delta=point(pose['ToesEnd'])-point(world(n['IKToesHandle']))
            p=point(world(n['IKToes']))+delta
            cmds.xform(n['IKToes'],ws=True,t=(p.x,p.y,p.z))
            # Use the nearest earlier key's Euler branch when adding adaptive
            # samples, so a new key cannot introduce a full-turn interpolation.
            for name in ('IKLeg','IKToes'):
                node=n[name]
                keys=sorted(set(cmds.keyframe(node,attribute=['rx','ry','rz'],q=True,timeChange=True) or []))
                earlier=[f for f in keys if f<frame]
                order=cmds.getAttr(node+'.rotateOrder')
                current=cmds.getAttr(node+'.rotate')[0]
                a=om.MEulerRotation(*[math.radians(v) for v in current],order)
                if earlier:
                    prev=cmds.getAttr(node+'.rotate',time=earlier[-1])[0]
                    b=om.MEulerRotation(*[math.radians(v) for v in prev],order)
                    a=a.closestSolution(b)
                    cmds.setAttr(node+'.rotate',*[math.degrees(v) for v in (a.x,a.y,a.z)],type='double3')
            for name,attrs in [('IKLeg',['tx','ty','tz','rx','ry','rz']),('PoleLeg',['tx','ty','tz']),('IKToes',['tx','ty','tz','rx','ry','rz'])]:
                cmds.setKeyframe(n[name],attribute=attrs,time=frame,inTangentType='linear',outTangentType='linear')
            for name in ('IKLeg','IKToes'):
                node=n[name]
                # Adaptive samples must not unwrap intentional turns outside
                # the requested bake range. Existing external keys are retained.
                keys=sorted(set(cmds.keyframe(node,attribute=['rx','ry','rz'],time=(frame,last),q=True,timeChange=True) or []))
                order=cmds.getAttr(node+'.rotateOrder')
                a=om.MEulerRotation(*[math.radians(v) for v in cmds.getAttr(node+'.rotate',time=frame)[0]],order)
                for later in [f for f in keys if f>frame]:
                    nxt=cmds.getAttr(node+'.rotate',time=later)[0]
                    c=om.MEulerRotation(*[math.radians(v) for v in nxt],order).closestSolution(a)
                    updated=[math.degrees(v) for v in (c.x,c.y,c.z)]
                    if max(abs(x-y) for x,y in zip(nxt,updated))<1e-4:break
                    for axis,val in zip('xyz',updated):
                        cmds.setKeyframe(node,attribute='r'+axis,time=later,value=val,inTangentType='linear',outTangentType='linear')
                    a=c
            cmds.setKeyframe(n['FKIKLeg'],attribute='FKIKBlend',time=frame,value=10,inTangentType='linear',outTangentType='step')
            cmds.currentTime(frame-.000001,edit=True);cmds.currentTime(frame,edit=True)
    for frame in times:
        cmds.currentTime(frame,edit=True)
        match_frame(frame)
    controls=[]
    from .rotation_curves import make_continuous
    for leg in legs:
        controls.extend([leg['nodes']['IKLeg'],leg['nodes']['IKToes']])
    make_continuous(cmds,om,controls,first,last)
    def frame_errors(frame):
        foot,knee,angle,knee_angle=0.,0.,0.,0.
        for leg in legs:
            pose=leg['poses'][frame]
            for name in ('Hip','Knee','Ankle','Toes','ToesEnd'):
                actual=world(leg['nodes'][name])
                error=(point(actual)-point(pose[name])).length()
                if name=='Knee':knee=max(knee,error)
                else:foot=max(foot,error)
                if name in ('Knee','Ankle','Toes'):
                    q=rot(actual)*rot(pose[name]).inverse()
                    error_angle=math.degrees(2*math.acos(min(1.,abs(q.w))))
                    if name=='Knee':knee_angle=max(knee_angle,error_angle)
                    else:angle=max(angle,error_angle)
        return foot,knee,angle,knee_angle
    refined=[]
    checked=set(times)
    def refine(left,right,depth=0):
        frame=(left+right)/2
        cmds.currentTime(frame,edit=True)
        # FK curves are retained. Briefly evaluate that original pose, then
        # restore IK at the same time without touching any FK control keys.
        for leg in legs:
            plug=leg['nodes']['FKIKLeg']+'.FKIKBlend'
            cmds.setKeyframe(plug,time=frame,value=0,inTangentType='linear',outTangentType='step')
            cmds.currentTime(frame-.000001,edit=True);cmds.currentTime(frame,edit=True)
            leg['poses'][frame]={name:world(leg['nodes'][name]) for name in ('Hip','Knee','Ankle','Toes','ToesEnd')}
            cmds.setKeyframe(plug,time=frame,value=10,inTangentType='linear',outTangentType='step')
            cmds.currentTime(frame-.000001,edit=True);cmds.currentTime(frame,edit=True)
        checked.add(frame)
        foot,knee,angle,knee_angle=frame_errors(frame)
        failed=(foot>position_tolerance*.5 or knee>knee_tolerance or angle>rotation_tolerance*.5
                or knee_angle>knee_rotation_tolerance*.5)
        if failed:
            match_frame(frame)
            refined.append(frame)
            foot,knee,angle,knee_angle=frame_errors(frame)
            if foot>position_tolerance or knee>knee_tolerance or angle>rotation_tolerance or knee_angle>knee_rotation_tolerance:
                raise RuntimeError('IK pose cannot reproduce FK at frame %g (foot %.5f cm, knee %.5f cm, angle %.5f degrees, knee angle %.5f degrees).'%(frame,foot,knee,angle,knee_angle))
        if failed or depth<1:
            if depth>=6:
                raise RuntimeError('IK interpolation did not converge near frame %g.'%frame)
            refine(left,frame,depth+1);refine(frame,right,depth+1)
    for left,right in zip(times,times[1:]):refine(left,right)
    make_continuous(cmds,om,controls,first,last)
    times=sorted(checked)
    stats=dict(enabled=True,samples=len(times),step_frames=step,adaptive_keys=len(refined),max_position_error_cm=0.,max_foot_position_error_cm=0.,max_knee_position_error_cm=0.,max_knee_rotation_error_degrees=0.,max_foot_rotation_error_degrees=0.,max_toe_rotation_error_degrees=0.,positions={})
    for frame in times:
        cmds.currentTime(frame,edit=True)
        for leg in legs:
            n=leg['nodes'];pose=leg['poses'][frame]
            for name in ('Hip','Knee','Ankle','Toes','ToesEnd'):
                actual=world(n[name]);err=(point(actual)-point(pose[name])).length()
                if err>stats['positions'].get(n[name],{}).get('error',-1):stats['positions'][n[name]]=dict(error=err,frame=frame)
                stats['max_position_error_cm']=max(stats['max_position_error_cm'],err)
                key='max_knee_position_error_cm' if name=='Knee' else 'max_foot_position_error_cm'
                stats[key]=max(stats[key],err)
                if name in ('Knee','Ankle','Toes'):
                    q=rot(actual)*rot(pose[name]).inverse()
                    angle=math.degrees(2*math.acos(min(1.,abs(q.w))))
                    key={'Ankle':'max_foot_rotation_error_degrees','Toes':'max_toe_rotation_error_degrees','Knee':'max_knee_rotation_error_degrees'}[name]
                    stats[key]=max(stats[key],angle)
    if stats['max_foot_position_error_cm']>position_tolerance or stats['max_knee_position_error_cm']>knee_tolerance or max(stats['max_foot_rotation_error_degrees'],stats['max_toe_rotation_error_degrees'])>rotation_tolerance or stats['max_knee_rotation_error_degrees']>knee_rotation_tolerance:
        raise RuntimeError('FK to IK verification failed: '+str(stats))
    return stats


def convert_scene(target, output, start=None, stop=None, step=.125):
    """Convert an existing file in an isolated Maya worker, saving a new scene."""
    from pathlib import Path
    from .scene_safety import validate_scene
    from .maya_engine import _maya
    target=Path(target).resolve();output=Path(output).resolve()
    if target==output or output.suffix.lower()!='.ma':
        raise ValueError('IK output must be a different .ma file.')
    if target.suffix.lower()!='.ma':raise ValueError('IK conversion requires a Maya ASCII scene.')
    validate_scene(target)
    cmds,_,om=_maya()
    cmds.file(str(target),open=True,force=True,executeScriptNodes=False)
    switches=[n for n in cmds.ls(type='transform',long=True) or []
              if n.rsplit('|',1)[-1].rsplit(':',1)[-1]=='FKIKLeg_L']
    if not switches:raise RuntimeError('No Advanced Skeleton leg switches found in scene.')
    namespaces={n.rsplit('|',1)[-1].rpartition(':')[0] for n in switches}
    fk_nodes=[]
    for ns in namespaces:
        for side in ('L','R'):
            for name in ('FKHip','FKKnee','FKAnkle','FKToes'):
                fk_nodes.extend(cmds.ls((ns+':' if ns else '')+name+'_'+side,long=True) or [])
    frames=cmds.keyframe(fk_nodes,attribute=['rx','ry','rz'],q=True,timeChange=True) or []
    if not frames:raise RuntimeError('No FK leg animation keys found.')
    first=min(frames) if start is None else start
    last=max(frames) if stop is None else stop
    result=bake_legs_to_ik(cmds,om,first,last,namespaces,step=step)
    cmds.currentTime(first,edit=True)
    cmds.playbackOptions(min=first,max=last,animationStartTime=first,animationEndTime=last)
    output.parent.mkdir(parents=True,exist_ok=True)
    cmds.file(rename=str(output));cmds.file(save=True,type='mayaAscii')
    if not output.is_file() or output.stat().st_size==0:raise RuntimeError('IK scene was not saved.')
    result.update(output_path=str(output),start=first,stop=last)
    return result
