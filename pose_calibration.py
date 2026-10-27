"""Reference-pose calibration based on anatomy, not zero rotate channels."""
import math
from .bone_matcher import leaf_name, resolve_name, ADV_JOINTS


def skin_bind_matrices(cmds, om, joints):
    """Skin inverse bind matrices include jointOrient, parent transforms and scale."""
    result={}
    joints=set(joints)
    for skin in cmds.ls(type='skinCluster') or []:
        for index in cmds.getAttr(skin+'.matrix',multiIndices=True) or []:
            influences=cmds.listConnections('%s.matrix[%d]'%(skin,index),source=True,destination=False,type='joint') or []
            for influence in influences:
                names=cmds.ls(influence,long=True) or []
                if len(names)==1 and names[0] in joints:
                    value=cmds.getAttr('%s.bindPreMatrix[%d]'%(skin,index))
                    result[names[0]]=om.MMatrix(value).inverse()
    return result


def restore_source_reference(cmds, source_path, reference_map):
    """Mixamo animation-only FBX defaults hold the unanimated bind translations."""
    from .fbx_metadata import metadata
    data=metadata(source_path)
    settings=data['settings']
    models={leaf_name(name):props for name,props in data['models'].items()}
    # Raw local coordinates are only reusable when the FBX and Maya units/axes agree.
    compatible=(settings.get('UpAxis',[1])[0]==1 and
                abs(settings.get('UnitScaleFactor',[1.])[0]-1.)<1e-8)
    if not compatible or not models:
        raise ValueError('源参考姿态需要 Y-up、厘米单位的 Mixamo FBX；不能用动画首帧代替绑定姿态。')
    for original,ref in reference_map.items():
        props=models.get(leaf_name(original))
        if props is None:
            raise ValueError('源 FBX 缺少参考姿态：'+original)
        for channel,field,default in [('rotate','Lcl Rotation',[0.,0.,0.]),('translate','Lcl Translation',None)]:
            value=props.get(field,default)
            if value is not None:
                cmds.setAttr(ref+'.'+channel,*value,type='double3')


def rotation(om,matrix):
    return om.MTransformationMatrix(matrix).rotation(asQuaternion=True).asMatrix()


def position(om,matrix):
    return om.MVector(matrix[12],matrix[13],matrix[14])


def calibrate(cmds,om,pairs,reference_map,target_nodes,mode):
    """Align each bone's reference aim; retain the actual bone/controller axis offset."""
    target_joints=[n for n in target_nodes if cmds.nodeType(n)=='joint']
    # ls(type=transform) may include joint descendants, depending on the Maya version.
    if mode=='maya':
        namespaces={t.rsplit('|',1)[-1].rpartition(':')[0] for _,t in pairs}
        target_joints=[n for n in cmds.ls(type='joint',long=True) or []
                       if n.rsplit('|',1)[-1].rpartition(':')[0] in namespaces]
    # FK controllers must be calibrated against their CURRENT driven joint frame.
    # A skin's stored bind pose may predate rig construction and differ from FK zero.
    binds=skin_bind_matrices(cmds,om,target_joints) if mode=='fbx' else {}
    world=lambda n:om.MMatrix(cmds.xform(n,q=True,ws=True,matrix=True))
    target_matrices={n:binds.get(n,world(n)) for n in target_joints}
    control_matrices={t:world(t) for _,t in pairs}
    source_matrices={leaf_name(s):world(ref) for s,ref in reference_map.items()}
    role_pairs={leaf_name(s):(s,t) for s,t in pairs}
    anatomy={}
    for role,(src,tgt) in role_pairs.items():
        if mode=='fbx':
            anatomy[role]=tgt
        else:
            ns=tgt.rsplit('|',1)[-1].rpartition(':')[0]
            name=ADV_JOINTS.get(role)
            if name:
                try:anatomy[role]=resolve_name((ns+':' if ns else '')+name,target_joints)
                except ValueError:pass
            anatomy.setdefault(role,tgt)
    # Available foot/head joints can be used for measurements even when their rows are blank.
    aliases=dict(ADV_JOINTS,LeftToe_End='ToesEnd_L',RightToe_End='ToesEnd_R')
    for side,suffix in [('Left','L'),('Right','R')]:
        for finger in ('Thumb','Index','Middle','Ring','Pinky'):
            aliases[side+'Hand'+finger+'4']=finger+'Finger4_'+suffix
    for role,alias in aliases.items():
        if role not in anatomy:
            candidates=[n for n in target_joints if leaf_name(n) in (role,alias)]
            if len(candidates)==1:anatomy[role]=candidates[0]
    def t_matrix(role):
        node=anatomy[role]
        return target_matrices.get(node,control_matrices.get(node,world(node)))
    def s_pos(role):return position(om,source_matrices[role])
    def t_pos(role):return position(om,t_matrix(role))

    # Map source world heading to the selected character's heading, keeping Y vertical.
    heading=om.MMatrix()
    for left,right in [('LeftUpLeg','RightUpLeg'),('LeftArm','RightArm')]:
        if all(r in source_matrices and r in anatomy for r in (left,right)):
            a=s_pos(left)-s_pos(right);a.y=0
            b=t_pos(left)-t_pos(right);b.y=0
            if a.length()>1e-8 and b.length()>1e-8:
                heading=a.rotateTo(b).asMatrix();break
    child_roles={'Hips':'Spine','Spine':'Spine1','Spine1':'Spine2','Spine2':'Neck','Neck':'Head','Head':'HeadTop_End'}
    for side in ('Left','Right'):
        child_roles.update({side+'Shoulder':side+'Arm',side+'Arm':side+'ForeArm',
                            side+'ForeArm':side+'Hand',side+'Hand':side+'HandMiddle1',
                            side+'UpLeg':side+'Leg',side+'Leg':side+'Foot',
                            side+'Foot':side+'ToeBase',side+'ToeBase':side+'Toe_End'})
        for finger in ('Thumb','Index','Middle','Ring','Pinky'):
            for segment in range(1,4):
                child_roles[side+'Hand'+finger+str(segment)]=side+'Hand'+finger+str(segment+1)
    aligned={};offset_degrees={};finger_frames={}
    for role,(src,tgt) in role_pairs.items():
        # Control rotation and deform joint rotation can themselves have different zero axes.
        rest=rotation(om,control_matrices[tgt] if mode=='maya' else t_matrix(role))
        child_role=child_roles.get(role)
        src_direction=None;tgt_direction=None
        if child_role in source_matrices:
            src_direction=(s_pos(child_role)-s_pos(role))*heading
            if child_role in anatomy:
                tgt_direction=t_pos(child_role)-t_pos(role)
            else:
                node=anatomy[role]
                descendants=cmds.listRelatives(node,children=True,fullPath=True,type='joint') or []
                for node in descendants:
                    point=position(om,target_matrices.get(node,world(node)))
                    if (point-t_pos(role)).length()>1e-8:
                        tgt_direction=point-t_pos(role);break
        correction=om.MMatrix()
        # Match the full palm frame, not source Middle versus target Thumb.
        palm_correction=None
        if role in ('LeftHand','RightHand'):
            from .hand_pose import palm_frame
            markers=[role,role+'Index1',role+'Ring1']
            if all(r in source_matrices and r in anatomy for r in markers):
                a=palm_frame(om,*[t_pos(r) for r in markers])
                b=palm_frame(om,*[s_pos(r) for r in markers])
                if a is not None and b is not None:palm_correction=a.inverse()*b*heading
        elif 'Hand' in role and child_role in source_matrices and child_role in anatomy:
            from .hand_pose import palm_frame,bone_frame
            hand=role.split('Hand',1)[0]+'Hand'
            markers=[hand,hand+'Index1',hand+'Ring1']
            if all(r in source_matrices and r in anatomy for r in markers):
                a=palm_frame(om,*[t_pos(r) for r in markers])
                b=palm_frame(om,*[s_pos(r) for r in markers])
                if a is not None and b is not None:
                    ta=bone_frame(om,t_pos(child_role)-t_pos(role),om.MVector(a[8],a[9],a[10]))
                    sb=bone_frame(om,s_pos(child_role)-s_pos(role),om.MVector(b[8],b[9],b[10]))
                    if ta is not None and sb is not None:
                        palm_correction=ta.inverse()*sb*heading
                        finger_frames[role]={'source':sb,'target':ta}
        # Foot/toe joint markers do not define the sole plane. Preserve their
        # actual bind orientations; independently aiming both creates a toe curl
        # even when the source has zero toe flexion.
        preserve_foot_bind=role in ('LeftFoot','RightFoot','LeftToeBase','RightToeBase') or (role=='Hips' and leaf_name(tgt)=='RootX_M')
        if palm_correction is not None:
            correction=palm_correction
        elif role not in ('LeftHand','RightHand') and not preserve_foot_bind and src_direction is not None and tgt_direction is not None and src_direction.length()>1e-8 and tgt_direction.length()>1e-8:
            q=tgt_direction.rotateTo(src_direction)
            correction=q.asMatrix()
            offset_degrees[role]=math.degrees(2*math.acos(min(1.,abs(q.w))))
        aligned[tgt]=rest*correction
    source_rotations={src:rotation(om,source_matrices[leaf_name(src)]) for src,_ in pairs}
    scale=1.;ground_source=None;ground_target=None
    if 'Hips' in source_matrices and 'Hips' in anatomy:
        feet=[r for r in ('LeftFoot','RightFoot','LeftToeBase','RightToeBase','LeftToe_End','RightToe_End')
              if r in source_matrices and r in anatomy]
        if feet:
            ground_source=min(s_pos(r).y for r in feet)
            ground_target=min(t_pos(r).y for r in feet)
            source_height=s_pos('Hips').y-ground_source
            target_height=t_pos('Hips').y-ground_target
            if source_height>1e-6 and target_height>1e-6:scale=target_height/source_height
            else:raise ValueError('绑定参考姿态的胯部离地高度无效。')
        elif 'Head' in source_matrices and 'Head' in anatomy:
            source_height=(s_pos('Head')-s_pos('Hips')).length()
            if source_height>1e-6:scale=(t_pos('Head')-t_pos('Hips')).length()/source_height
    root_reference=None
    if 'Hips' in role_pairs:
        src,tgt=role_pairs['Hips']
        root_reference=position(om,control_matrices[tgt] if mode=='maya' else t_matrix('Hips'))
    def quaternion_values(matrix):
        q=om.MTransformationMatrix(matrix).rotation(asQuaternion=True)
        return [q.x,q.y,q.z,q.w]
    foot_roles=[r for r in ('LeftFoot','RightFoot','LeftToeBase','RightToeBase') if r in source_matrices and r in anatomy]
    return dict(source_rest=source_rotations,target_rest=aligned,heading=heading,scale=scale,
                source_nodes={leaf_name(s):s for s in reference_map},
                finger_reference_frames=finger_frames,
                target_control_reference={t:rotation(om,m) for t,m in control_matrices.items()},
                target_joint_reference={r:rotation(om,t_matrix(r)) for r in anatomy},
                source_positions={r:position(om,m) for r,m in source_matrices.items()},
                target_positions={r:t_pos(r) for r in anatomy},target_anatomy=anatomy,
                source_root_reference=s_pos('Hips') if 'Hips' in source_matrices else None,
                target_root_reference=root_reference,
                diagnostics=dict(reference_aim_corrections_degrees=offset_degrees,
                                 hand_orientation_policy='full_palm_and_finger_frames',
                                 foot_orientation_policy='target_bind_plus_source_delta',
                                 foot_end_references={r:dict(source_position=list(s_pos(r)),target_position=list(t_pos(r))) for r in ('LeftToe_End','RightToe_End') if r in source_matrices and r in anatomy},
                                 foot_references={r:dict(source_position=list(s_pos(r)),target_position=list(t_pos(r)),
                                                         source_rotation=quaternion_values(source_matrices[r]),target_rotation=quaternion_values(t_matrix(r))) for r in foot_roles},
                                 heading_world_matrix=list(heading),
                                 source_ground=ground_source,target_ground=ground_target,
                                 source_bind_hips_y=s_pos('Hips').y if 'Hips' in source_matrices else None,
                                 target_bind_hips_y=root_reference.y if root_reference is not None else None))
