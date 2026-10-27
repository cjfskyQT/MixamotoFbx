"""Maya-native calibrated world-rotation transfer and FK baking."""
from pathlib import Path
import math
import uuid
from .bone_matcher import leaf_name, resolve_name, auto_match_adv, auto_match_controllers, upgrade_adv_root_mapping
from .scene_safety import validate_scene


def _maya():
    from maya import cmds, mel
    from maya.api import OpenMaya as om
    if not cmds.pluginInfo('fbxmaya', query=True, loaded=True):
        cmds.loadPlugin('fbxmaya', quiet=True)
    return cmds, mel, om


def _import(path, namespace=None):
    cmds, mel, _ = _maya()
    validate_scene(path)
    from .fbx_metadata import frame_rate
    cmds.currentUnit(time=('%gfps' % frame_rate(path)))
    mel.eval('FBXImportSetMayaFrameRate -v true;')
    mel.eval('FBXImportMode -v "add";')
    kwargs = dict(i=True, type='FBX', ignoreVersion=True, returnNewNodes=True,
                  mergeNamespacesOnClash=False, executeScriptNodes=False)
    if namespace:
        kwargs['namespace'] = namespace
    return cmds.file(str(Path(path).resolve()), **kwargs) or []


def _fps(cmds):
    unit = cmds.currentUnit(query=True, time=True)
    table = {'film':24., 'pal':25., 'ntsc':30., 'show':48., 'palf':50., 'ntscf':60.}
    return float(unit[:-3]) if unit.endswith('fps') else table.get(unit, 30.)


def inspect_fbx(path):
    cmds, _, _ = _maya()
    cmds.file(new=True, force=True)
    _import(path)
    joints = cmds.ls(type='joint', long=True) or []
    bones, curves, times = {}, [], []
    for node in joints:
        name = node.rsplit('|',1)[-1]
        parent = cmds.listRelatives(node, parent=True, type='joint') or []
        bones[name] = dict(name=name, parent=parent[0] if parent else None,
                           rest_translation=cmds.getAttr(node+'.translate')[0],
                           rest_rotation_euler=cmds.getAttr(node+'.jointOrient')[0],
                           rest_scale=cmds.getAttr(node+'.scale')[0])
        for prop, prefix in [('T','translate'), ('R','rotate')]:
            for axis in 'xyz':
                plug = node+'.'+prefix+axis.upper()
                frames = cmds.keyframe(plug, query=True, timeChange=True) or []
                if frames:
                    values = cmds.keyframe(plug, query=True, valueChange=True)
                    curves.append(dict(bone=name, prop=prop, axis=axis, frames=frames, values=values))
                    times.extend(frames)
    return dict(bones=bones, root=next((n for n,b in bones.items() if not b['parent']), None),
                start=min(times) if times else 0, stop=max(times) if times else 0,
                fps=_fps(cmds), curves=curves)


def force_fk(cmds, target_nodes, start, stop):
    """Resolve switches within the selected character and hold zero every frame."""
    from .adv_scan import ADV_FKIK_SWITCHES
    namespaces = {n.rsplit('|',1)[-1].rpartition(':')[0] for n in target_nodes}
    namespaces = namespaces or {''}
    required = set(ADV_FKIK_SWITCHES[:4])
    for namespace in namespaces:
        for name in ADV_FKIK_SWITCHES:
            qualified = (namespace+':' if namespace else '')+name
            matches = cmds.ls(qualified, long=True) or []
            if not matches:
                if name in required:
                    raise RuntimeError('找不到 FK/IK 转换器：'+qualified)
                continue
            if len(matches) != 1:
                raise RuntimeError('FK/IK 转换器名称不唯一：'+qualified)
            plug=matches[0]+'.FKIKBlend'
            if not cmds.objExists(plug) or cmds.getAttr(plug, lock=True):
                raise RuntimeError('FKIKBlend 不存在或被锁定：'+plug)
            cmds.cutKey(plug, time=(start, stop), clear=True)
            for frame in range(start, stop+1):
                cmds.setKeyframe(plug, time=frame, value=0,
                                 inTangentType='linear', outTangentType='step')


def _rotation(cmds, om, node):
    return om.MTransformationMatrix(om.MMatrix(cmds.xform(node,q=True,ws=True,matrix=True))).rotation(asQuaternion=True).asMatrix()


def _walk(cmds, root):
    result=[root]
    for child in cmds.listRelatives(root,children=True,fullPath=True,type='joint') or []:
        result.extend(_walk(cmds,child))
    return result


def _retarget_impl(source, target=None, output=None, mapping=None, mode='fbx', start=None, stop=None, force_fk_enabled=True, contact_compensation=True, legs_ik=True):
    cmds, mel, om = _maya()
    if output:
        destination = Path(output).resolve()
        if destination == Path(source).resolve() or (target and destination == Path(target).resolve()):
            raise ValueError('输出不能覆盖源文件或目标绑定文件。')
    if target:
        validate_scene(target)
        cmds.file(new=True, force=True)
        if mode=='maya':
            cmds.file(str(Path(target).resolve()),open=True,force=True,executeScriptNodes=False)
        else:
            _import(target)
    target_nodes = cmds.ls(type='joint' if mode=='fbx' else 'transform',long=True) or []
    target_roots = cmds.ls(assemblies=True,long=True) or []
    token='_CR_'+uuid.uuid4().hex[:8]
    source_nodes=_import(source,token)
    source_joints=[n for n in cmds.ls(type='joint',long=True) or [] if n.rsplit('|',1)[-1].startswith(token+':')]
    if not source_joints:
        raise RuntimeError('源 FBX 没有骨骼。')
    times=cmds.keyframe(source_joints,query=True,timeChange=True) or []
    if not times:
        raise RuntimeError('源 FBX 没有动画关键帧。')
    first=int(math.floor(min(times))) if start is None else int(start)
    last=int(math.ceil(max(times))) if stop is None else int(stop)
    if first>last or (start is not None and first<min(times)-1e-4) or (stop is not None and last>max(times)+1e-4):
        raise ValueError('帧区间无效或超出源动画范围。')
    if mapping is None:
        matcher=auto_match_adv if mode=='fbx' else auto_match_controllers
        mapping=matcher(source_joints,target_nodes).active_targets()
    mapping=dict(mapping)
    root_mapping_migrated=False
    if mode=='maya':
        # Upgrade saved mappings created before the whole-pelvis control fix.
        mapping,root_mapping_migrated=upgrade_adv_root_mapping(mapping,target_nodes)
    pairs=[]
    used=set()
    for source_name,target_name in mapping.items():
        src=resolve_name(leaf_name(source_name),source_joints)
        tgt=resolve_name(target_name,target_nodes)
        if tgt in used:
            raise ValueError('多个源骨骼不能覆盖同一目标：'+tgt)
        used.add(tgt)
        # A mapped bone may inherit animated world rotation without owning keys.
        # Hips can also have translation-only animation; both must be transferred.
        for axis in 'XYZ':
            if not cmds.getAttr(tgt+'.rotate'+axis,settable=True):
                # Existing animCurves are replaceable, rig connections are not.
                incoming=cmds.listConnections(tgt+'.rotate'+axis,source=True,destination=False) or []
                if cmds.getAttr(tgt+'.rotate'+axis,lock=True) or any(not cmds.nodeType(n).startswith('animCurve') for n in incoming):
                    raise RuntimeError('目标旋转通道被锁定或由其它节点驱动：'+tgt+'.rotate'+axis)
        pairs.append((src,tgt))
    if not pairs:
        raise RuntimeError('没有可烘焙的有效映射和动画通道。')
    cmds.currentTime(first,edit=True)
    if mode=='maya' and (force_fk_enabled or legs_ik):
        force_fk(cmds,[t for _,t in pairs],first,last)
        cmds.dgdirty(allPlugs=True)
        cmds.currentTime(first,edit=True,update=True)
    # Mixamo exports use zero local rotations as their bind reference.
    source_roots=[n for n in source_joints if not cmds.listRelatives(n,parent=True,type='joint')]
    references=[]; reference_map={}
    helper_group=cmds.group(empty=True,name=token+'_helpers')
    constraints=[]
    try:
        for root in source_roots:
            clone=cmds.duplicate(root,renameChildren=True,returnRootsOnly=True)[0]
            references.append(clone)
            originals=_walk(cmds,root); clones=_walk(cmds,clone)
            if len(originals)!=len(clones):
                raise RuntimeError('无法建立源骨架参考姿态。')
            for orig,ref in zip(originals,clones):
                cmds.setAttr(ref+'.rotate',0,0,0,type='double3')
                reference_map[orig]=ref
        from .pose_calibration import restore_source_reference, calibrate
        restore_source_reference(cmds, source, reference_map)
        if mode=='fbx':
            for _,tgt in pairs:
                # Keep the imported bind rotation; zero rotate is not a universal pose.
                cmds.cutKey(tgt,attribute=['rx','ry','rz'],clear=True)
        calibration=calibrate(cmds,om,pairs,reference_map,target_nodes,mode)
        source_rest=calibration['source_rest']
        target_rest=calibration['target_rest']
        heading=calibration['heading']
        scale=calibration['scale']
        roots=[(s,t) for s,t in pairs if leaf_name(s)=='Hips']
        src_bind_origin=calibration['source_root_reference']
        tgt_bind_origin=calibration['target_root_reference']
        helpers={t:cmds.createNode('transform',name=token+'_drive',parent=helper_group) for _,t in pairs}
        bake_step=.25 if contact_compensation else 1.
        divisions=4 if contact_compensation else 1
        dense_roles={'Hips','LeftUpLeg','LeftLeg','LeftFoot','LeftToeBase','RightUpLeg','RightLeg','RightFoot','RightToeBase'}
        for sample in range((last-first)*divisions+1):
            frame=first+sample/divisions
            cmds.currentTime(frame,edit=True)
            for src,tgt in pairs:
                if frame!=int(frame) and leaf_name(src) not in dense_roles:continue
                desired=target_rest[tgt]*heading.inverse()*source_rest[src].inverse()*_rotation(cmds,om,src)*heading
                rotation=om.MTransformationMatrix(desired).rotation()
                angles=[math.degrees(v) for v in (rotation.x,rotation.y,rotation.z)]
                cmds.setAttr(helpers[tgt]+'.rotate',*angles,type='double3')
                cmds.setKeyframe(helpers[tgt],attribute=['rx','ry','rz'],time=frame,inTangentType='linear',outTangentType='linear')
                if leaf_name(src)=='Hips':
                    position=om.MVector(cmds.xform(src,query=True,ws=True,translation=True))
                    # Use the same bind-space origin on all axes. Subtracting the
                    # first animation frame silently recenters each clip differently.
                    travel=(position-src_bind_origin)*heading
                    desired_position=tgt_bind_origin+travel*scale
                    cmds.setAttr(helpers[tgt]+'.translate',desired_position.x,desired_position.y,desired_position.z,type='double3')
                    cmds.setKeyframe(helpers[tgt],attribute=['tx','ty','tz'],time=frame,inTangentType='linear',outTangentType='linear')
        for src,tgt in pairs:
            constraints.extend(cmds.orientConstraint(helpers[tgt],tgt,maintainOffset=False))
            if leaf_name(src)=='Hips':
                if any(cmds.getAttr(tgt+'.translate'+a,lock=True) for a in 'XYZ'):
                    raise RuntimeError('根控制器平移被锁定，无法生成根运动：'+tgt)
                constraints.extend(cmds.pointConstraint(helpers[tgt],tgt,maintainOffset=False))
        anatomy_stats={}
        if mode=='maya':
            from .anatomy_solver import solve_aims
            anatomy_stats=solve_aims(cmds,om,calibration,pairs,helpers,first,last)
        from .hand_pose import solve_hands,palm_frame
        hand_stats=solve_hands(cmds,om,calibration,pairs,helpers,first,last)
        from .rotation_curves import make_continuous
        make_continuous(cmds,om,list(helpers.values()),first,last)
        contact_stats=dict(enabled=bool(contact_compensation),frames_adjusted=0,maximum_adjustment_cm=0.)
        if roots and contact_compensation and calibration['diagnostics']['source_ground'] is not None:
            from .foot_contact import solve_contacts
            contact_stats=solve_contacts(cmds,om,calibration,pairs,reference_map,helpers,first,last,_fps(cmds))
        make_continuous(cmds,om,list(helpers.values()),first,last)
        targets=[t for _,t in pairs]
        cmds.bakeResults(targets,time=(first,last),simulation=True,sampleBy=bake_step,
                         attribute=['rx','ry','rz'],preserveOutsideKeys=True,
                         sparseAnimCurveBake=False,disableImplicitControl=False)
        for _,tgt in roots:
            cmds.bakeResults(tgt,time=(first,last),simulation=True,sampleBy=bake_step,
                             attribute=['tx','ty','tz'],preserveOutsideKeys=True,sparseAnimCurveBake=False)
        cmds.delete(constraints); constraints=[]
        make_continuous(cmds,om,targets,first,last)
        for _,tgt in roots:
            cmds.keyTangent(tgt,attribute=['tx','ty','tz'],time=(first,last),inTangentType='linear',outTangentType='linear')
        max_angle_error=0.
        for frame in range(first,last+1):
            cmds.currentTime(frame,edit=True)
            for tgt in targets:
                delta=_rotation(cmds,om,tgt)*_rotation(cmds,om,helpers[tgt]).inverse()
                q=om.MTransformationMatrix(delta).rotation(asQuaternion=True)
                angle=math.degrees(2*math.acos(min(1.,abs(q.w))))
                max_angle_error=max(max_angle_error,angle)
        if max_angle_error>0.1:
            raise RuntimeError('烘焙后世界旋转验证失败，最大误差 %.3f 度。'%max_angle_error)
        max_position_error=0.
        max_baked_foot_error=0.
        for frame,goals in calibration.get('foot_trajectory_targets',{}).items():
            cmds.currentTime(frame,edit=True)
            for role,goal in goals.items():
                actual=om.MVector(cmds.xform(calibration['target_anatomy'][role],q=True,ws=True,t=True))
                max_baked_foot_error=max(max_baked_foot_error,(actual-goal).length())
        if max_baked_foot_error>.05:
            raise RuntimeError('烘焙后脚部轨迹验证失败，最大误差 %.4f 厘米。'%max_baked_foot_error)
        max_pelvis_rotation_error=0.
        max_hip_frame_error=0.
        max_anatomy_direction_error=0.
        max_palm_error=0.
        max_finger_error=0.
        max_finger_plane_error=0.
        role_sources={leaf_name(s):s for s,_ in pairs}
        for frame in range(first,last+1):
            cmds.currentTime(frame,edit=True)
            source_roles=calibration['source_nodes'];anatomy=calibration['target_anatomy']
            point=lambda n:om.MVector(cmds.xform(n,q=True,ws=True,t=True))
            for side in ('Left','Right'):
                role=side+'Hand';markers=[role,role+'Index1',role+'Ring1']
                if role in role_sources and all(r in source_roles and r in anatomy for r in markers):
                    actual=palm_frame(om,*[point(anatomy[r]) for r in markers])
                    expected=palm_frame(om,*[point(source_roles[r]) for r in markers])
                    if actual is not None and expected is not None:
                        q=om.MTransformationMatrix(actual.inverse()*expected*heading).rotation(asQuaternion=True)
                        max_palm_error=max(max_palm_error,math.degrees(2*math.acos(min(1.,abs(q.w)))))
                for finger in ('Thumb','Index','Middle','Ring','Pinky'):
                    for segment in range(1,4):
                        r=side+'Hand'+finger+str(segment);child=side+'Hand'+finger+str(segment+1)
                        if r not in role_sources or not all(n in source_roles and n in anatomy for n in (r,child)):continue
                        a=point(anatomy[child])-point(anatomy[r]);b=(point(source_roles[child])-point(source_roles[r]))*heading
                        if min(a.length(),b.length())>1e-8:max_finger_error=max(max_finger_error,math.degrees(a.angle(b)))
                        if r in calibration['finger_reference_frames']:
                            frames=calibration['finger_reference_frames'][r];a,b=frames['target'],frames['source']
                            actual=om.MVector(a[8],a[9],a[10])*calibration['target_joint_reference'][r].inverse()*_rotation(cmds,om,anatomy[r])
                            expected=om.MVector(b[8],b[9],b[10])*calibration['source_rest'][role_sources[r]].inverse()*_rotation(cmds,om,source_roles[r])*heading
                            max_finger_plane_error=max(max_finger_plane_error,math.degrees(actual.angle(expected)))
            for role,child in [('Spine','Spine1'),('Spine1','Spine2'),('Spine2','Neck'),('Neck','Head')]:
                anatomy=calibration['target_anatomy']
                if mode!='maya' or not all(r in role_sources and r in anatomy for r in (role,child)):
                    continue
                actual=om.MVector(cmds.xform(anatomy[child],q=True,ws=True,t=True))-om.MVector(cmds.xform(anatomy[role],q=True,ws=True,t=True))
                expected=(om.MVector(cmds.xform(role_sources[child],q=True,ws=True,t=True))-om.MVector(cmds.xform(role_sources[role],q=True,ws=True,t=True)))*heading
                if min(actual.length(),expected.length())>1e-8:
                    max_anatomy_direction_error=max(max_anatomy_direction_error,math.degrees(actual.angle(expected)))
            for _,tgt in roots:
                actual=om.MVector(cmds.xform(tgt,q=True,ws=True,t=True))
                expected=om.MVector(cmds.xform(helpers[tgt],q=True,ws=True,t=True))
                max_position_error=max(max_position_error,(actual-expected).length())
                if mode=='maya' and leaf_name(tgt)=='RootX_M' and 'Hips' in calibration['target_anatomy']:
                    control_bind=calibration['target_control_reference'][tgt]
                    delta=control_bind.inverse()*_rotation(cmds,om,helpers[tgt])
                    expected_rotation=calibration['target_joint_reference']['Hips']*delta
                    joint=calibration['target_anatomy']['Hips']
                    q=om.MTransformationMatrix(_rotation(cmds,om,joint)*expected_rotation.inverse()).rotation(asQuaternion=True)
                    max_pelvis_rotation_error=max(max_pelvis_rotation_error,math.degrees(2*math.acos(min(1.,abs(q.w)))))
                    if all(r in calibration['target_positions'] for r in ('LeftUpLeg','RightUpLeg')):
                        anatomy=calibration['target_anatomy']
                        vector=om.MVector(cmds.xform(anatomy['LeftUpLeg'],q=True,ws=True,t=True))-om.MVector(cmds.xform(anatomy['RightUpLeg'],q=True,ws=True,t=True))
                        expected_vector=(calibration['target_positions']['LeftUpLeg']-calibration['target_positions']['RightUpLeg'])*delta
                        max_hip_frame_error=max(max_hip_frame_error,(vector-expected_vector).length())
        if max_position_error>.001:
            raise RuntimeError('烘焙后世界位移验证失败，最大误差 %.4f 厘米。'%max_position_error)
        if max_pelvis_rotation_error>.1 or max_hip_frame_error>.05:
            raise RuntimeError('ADV 实际骨盆未正确跟随整体根运动：旋转误差 %.3f 度，左右胯误差 %.4f 厘米。请检查 FKRoot/次级控制器是否残留旧动画或偏移。'%(max_pelvis_rotation_error,max_hip_frame_error))
        if max_anatomy_direction_error>.1:
            raise RuntimeError('ADV 脊柱/颈部实际关节方向验证失败，最大误差 %.3f 度。'%max_anatomy_direction_error)
        if max_palm_error>.1 or max_finger_error>.1 or max_finger_plane_error>.1:
            raise RuntimeError('实际手掌/手指验证失败：手掌 %.3f 度，手指方向 %.3f 度，手指扭转 %.3f 度。'%(max_palm_error,max_finger_error,max_finger_plane_error))
        ik_stats=dict(enabled=False)
        if mode=='maya' and legs_ik:
            from .leg_ik import bake_legs_to_ik
            namespaces={t.rsplit('|',1)[-1].rpartition(':')[0] for t in targets}
            ik_stats=bake_legs_to_ik(cmds,om,first,last,namespaces,step=min(bake_step,.125))
        writes=sum(cmds.keyframe(t,attribute=['rx','ry','rz'],query=True,keyframeCount=True) or 0 for t in targets)
        if not writes:
            raise RuntimeError('烘焙未产生旋转关键帧，拒绝报告成功。')
    finally:
        if constraints: cmds.delete(constraints)
        if cmds.objExists(helper_group): cmds.delete(helper_group)
        for ref in references:
            if cmds.objExists(ref): cmds.delete(ref)
        # Remove the imported source character, including any X Bot mesh.
        if cmds.namespace(exists=token):
            cmds.namespace(removeNamespace=token,deleteNamespaceContent=True)
    cmds.currentTime(first,edit=True)
    cmds.playbackOptions(min=first,max=last,animationStartTime=first,animationEndTime=last)
    if output:
        dest=Path(output).resolve()
        if target and dest==Path(target).resolve() or dest==Path(source).resolve():
            raise ValueError('输出不能覆盖源文件或目标绑定文件。')
        dest.parent.mkdir(parents=True,exist_ok=True)
        if mode=='fbx':
            cmds.select(target_roots,replace=True)
            # FK curves are already baked, including subframes. Export baking
            # would resample them back to whole frames and reintroduce sliding.
            mel.eval('FBXExportBakeComplexAnimation -v false;')
            if mel.eval('exists "FBXExportBakeResampleAnimation"'):
                mel.eval('FBXExportBakeResampleAnimation -v false;')
            mel.eval('FBXExportBakeComplexStart -v %d;'%first)
            mel.eval('FBXExportBakeComplexEnd -v %d;'%last)
            mel.eval('FBXExportConstraints -v false;')
            cmds.file(str(dest),force=True,type='FBX export',exportSelected=True,options='v=0;')
        else:
            cmds.file(rename=str(dest)); cmds.file(save=True,type='mayaAscii')
        if not dest.is_file() or dest.stat().st_size==0:
            raise RuntimeError('输出文件未成功写入：'+str(dest))
    return dict(output_path=str(Path(output).resolve()) if output else None,bones_driven=len(pairs),
                key_count=writes,start=first,stop=last,root_scale=scale,
                max_rotation_error_degrees=max_angle_error,max_position_error_cm=max_position_error,
                bake_step_frames=bake_step,max_baked_foot_error_cm=max_baked_foot_error,
                max_pelvis_rotation_error_degrees=max_pelvis_rotation_error,max_hip_frame_error_cm=max_hip_frame_error,
                root_mapping_migrated=root_mapping_migrated,
                anatomy_aims=anatomy_stats,
                hands=hand_stats,legs_ik=ik_stats,
                max_palm_frame_error_degrees=max_palm_error,max_finger_direction_error_degrees=max_finger_error,
                max_finger_plane_error_degrees=max_finger_plane_error,
                max_anatomy_direction_error_degrees=max_anatomy_direction_error,
                contact_compensation=contact_stats,calibration=calibration['diagnostics'])


def retarget(*args, **kwargs):
    """Also clean imported helper namespaces if preflight validation fails."""
    cmds, _, _ = _maya()
    before=set(cmds.namespaceInfo(listOnlyNamespaces=True,recurse=True) or [])
    try:
        return _retarget_impl(*args, **kwargs)
    finally:
        after=set(cmds.namespaceInfo(listOnlyNamespaces=True,recurse=True) or [])
        for namespace in sorted(after-before,key=len,reverse=True):
            if namespace.split(':')[0].startswith('_CR_') and cmds.namespace(exists=namespace):
                cmds.namespace(removeNamespace=namespace,deleteNamespaceContent=True)


def dispatch(payload):
    operation=payload.pop('operation')
    if operation=='inspect': return inspect_fbx(payload['path'])
    if operation=='retarget': return retarget(**payload)
    if operation=='legs_ik':
        from .leg_ik import convert_scene
        return convert_scene(**payload)
    raise ValueError('Unknown operation: '+operation)
