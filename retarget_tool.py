"""Mixamo to ADV/custom FBX or ADV FK controllers, using installed Maya."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
from dataclasses import dataclass
from typing import Optional

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _bootstrap import load_package
    __package__ = load_package()
from .fbx_reader import load, FbxUnavailable
from .bone_matcher import (BoneMap, MixamoTarget, auto_match_adv,
    auto_match_by_name, auto_match_controllers, verify_map, duplicate_targets,
    skeleton_signature, leaf_name)
from .maya_runtime import run_job
from .scene_safety import scan_text as _scan_ma_for_threats, validate_scene
from .maya_driver import list_adv_controllers_from_ma, auto_map_from_ma

@dataclass
class RetargetResult:
    mode: str
    output_path: Optional[str] = None
    bones_driven: int = 0
    skipped: int = 0
    key_count: int = 0


def save_bone_map(bm, path):
    payload = dict(version=3,target_namespace=bm.target_namespace,
                   target_signature=bm.target_signature,
                   rows=[dict(mixamo=k,mixamo_full=r.mixamo_full,target=r.target,enabled=r.enabled)
                         for k,r in bm.entries.items()])
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')


def load_bone_map(path, expected_signature=None):
    try:
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('version') not in (1,2,3): return None
        if expected_signature and data.get('target_signature')!=expected_signature: return None
        bm=BoneMap(target_namespace=data.get('target_namespace',''),target_signature=data.get('target_signature',''))
        for row in data['rows']:
            key=leaf_name(row['mixamo'])
            bm.entries[key]=MixamoTarget(key,row.get('mixamo_full',row['mixamo']),row.get('target',''),bool(row.get('enabled',False)))
        return bm
    except (OSError,ValueError,KeyError,TypeError):
        return None


def _validated_map(bm, source_names, target_names):
    issues=verify_map(bm,target_names)
    active=bm.active_targets()
    if not active: raise ValueError('没有有效映射。请至少填写一个目标骨骼。')
    missing=set(active)-{leaf_name(n) for n in source_names}
    if missing: issues.append('源动画缺少骨骼：'+', '.join(sorted(missing)))
    dups=duplicate_targets(bm)
    if dups: issues.append('目标被重复映射：'+', '.join(dups))
    if issues: raise ValueError('\n'.join(issues))
    bm.target_signature=skeleton_signature(target_names)
    return active


def retarget_fbx_adv(source_fbx,target_fbx,output_fbx,*,start_frame=None,end_frame=None,contact_compensation=True):
    # The worker discovers and validates the real scene before writing any keys.
    result=run_job(dict(operation='retarget',source=str(Path(source_fbx).resolve()),
                        target=str(Path(target_fbx).resolve()),output=output_fbx,
                        mode='fbx',start=start_frame,stop=end_frame,contact_compensation=contact_compensation))
    return RetargetResult('fbx_adv',result['output_path'],result['bones_driven'],key_count=result['key_count'])


def retarget_fbx_custom(source_fbx,target_fbx,output_fbx,*,bone_map,start_frame=None,end_frame=None,contact_compensation=True):
    src,tgt=load(source_fbx),load(target_fbx)
    active=_validated_map(bone_map,src.skeleton.bones,tgt.skeleton.bones)
    result=run_job(dict(operation='retarget',source=str(Path(source_fbx).resolve()),
                        target=str(Path(target_fbx).resolve()),output=output_fbx,mapping=active,
                        mode='fbx',start=start_frame,stop=end_frame,contact_compensation=contact_compensation))
    return RetargetResult('fbx_custom',result['output_path'],result['bones_driven'],
                          sum(not r.is_active() for r in bone_map.entries.values()),result['key_count'])


def _maya_running():
    try:
        from maya import cmds
        return bool(cmds.about(version=True))
    except (ImportError,RuntimeError,AttributeError): return False


def retarget_maya_scene(source_fbx,*,bone_map=None,start_frame=None,end_frame=None,
                        use_command_port=False,host='127.0.0.1',port=7001,force_fk=True,
                        ma_target=None,save_ma_path=None,allow_unsafe_ma=False,contact_compensation=True,legs_ik=True):
    if allow_unsafe_ma:
        raise ValueError('不支持绕过恶意脚本检查。请使用可信的干净绑定文件。')
    if ma_target: validate_scene(ma_target)
    payload=dict(source=str(Path(source_fbx).resolve()),target=str(Path(ma_target).resolve()) if ma_target else None,
                 output=str(Path(save_ma_path).resolve()) if save_ma_path else None,
                 mode='maya',mapping=bone_map.active_targets() if bone_map else None,
                 start=start_frame,stop=end_frame,force_fk_enabled=force_fk,contact_compensation=contact_compensation,legs_ik=legs_ik)
    if use_command_port:
        from .maya_driver import run_remote_job
        result=run_remote_job(payload,host,port)
    elif _maya_running():
        from .maya_engine import retarget
        from maya import cmds
        if ma_target and cmds.file(query=True,modified=True):
            raise RuntimeError('当前 Maya 场景有未保存修改。请保存场景后再加载目标绑定。')
        cmds.undoInfo(openChunk=True,chunkName='CapoeiraRetarget')
        try:
            result=retarget(**payload)
        except Exception:
            cmds.undoInfo(closeChunk=True)
            cmds.undo()
            raise
        else:
            cmds.undoInfo(closeChunk=True)
    else:
        if not ma_target: raise RuntimeError('外部运行需要选择 MA 文件，或连接已打开的 Maya。')
        result=run_job(dict(operation='retarget',**payload))
    return RetargetResult('maya',result['output_path'],result['bones_driven'],key_count=result['key_count'])


def _load_or_build_bone_map(source_fbx,target_path=None,*,maya_scene=False,ui=False,cache_path=None,ma_target=None):
    src=load(source_fbx)
    if ma_target:
        target_bones=list_adv_controllers_from_ma(ma_target)
    elif maya_scene:
        from maya import cmds
        target_bones=cmds.ls(type='transform',long=True) or []
    else:
        target_bones=list(load(target_path).skeleton.bones)
    signature=skeleton_signature(target_bones)
    bm=load_bone_map(cache_path,signature) if cache_path else None
    if bm is None:
        bm=(auto_match_controllers if maya_scene else auto_match_adv)(src.skeleton.bones,target_bones)
    if ui:
        from .qt_compat import QtWidgets
        from .ui.matcher_dialog import MatcherDialog
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        dlg=MatcherDialog(bm,target_bones)
        if dlg.exec()!=dlg.DialogCode.Accepted: raise RuntimeError('用户取消了匹配。')
        bm=dlg.result_map()
    _validated_map(bm,src.skeleton.bones,target_bones)
    if cache_path: save_bone_map(bm,cache_path)
    return bm


def main_window():
    from .ui.main_window import main_window as launch
    return launch()


def _run_cli():
    p=argparse.ArgumentParser(description='Mixamo 动画重定向（需要已安装 Maya）')
    p.add_argument('--source',required=True)
    p.add_argument('--target')
    p.add_argument('--output')
    p.add_argument('--mode',choices=['adv','custom','maya'],default='adv')
    p.add_argument('--ui',action='store_true',help='打开手动匹配面板')
    p.add_argument('--ma-target')
    p.add_argument('--map-cache')
    p.add_argument('--start',type=int)
    p.add_argument('--end',type=int)
    p.add_argument('--no-fk',action='store_true')
    p.add_argument('--legs-fk',action='store_true',help='Maya 输出保留双腿 FK；默认匹配并烘焙双腿 IK')
    p.add_argument('--no-contact-compensation',action='store_true',help='关闭脚部轨迹修正，只适配胯部轨迹')
    p.add_argument('--use-command-port',action='store_true')
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--port',type=int,default=7001)
    a=p.parse_args()
    if a.mode!='maya' and not a.target: p.error('--target is required')
    output=a.output or str(Path(a.source).with_name(Path(a.source).stem+('_baked.ma' if a.mode=='maya' else '_retarget.fbx')))
    kwargs=dict(start_frame=a.start,end_frame=a.end,contact_compensation=not a.no_contact_compensation)
    if a.mode=='adv': result=retarget_fbx_adv(a.source,a.target,output,**kwargs)
    elif a.mode=='custom':
        bm=_load_or_build_bone_map(a.source,a.target,ui=a.ui,cache_path=a.map_cache)
        result=retarget_fbx_custom(a.source,a.target,output,bone_map=bm,**kwargs)
    else:
        ma=a.ma_target or a.target
        bm=_load_or_build_bone_map(a.source,maya_scene=True,ui=a.ui,cache_path=a.map_cache,ma_target=ma) if (ma or _maya_running()) else None
        result=retarget_maya_scene(a.source,bone_map=bm,ma_target=ma,save_ma_path=output,
                                   use_command_port=a.use_command_port,host=a.host,port=a.port,
                                   force_fk=not a.no_fk,legs_ik=not a.legs_fk,**kwargs)
    print(result)
    return 0


def run_gui():
    from .qt_compat import QtWidgets
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win=main_window()
    return app.exec()


if __name__=='__main__':
    if '--ui-gui' in sys.argv or sys.argv[1:]==['--ui']:
        sys.exit(run_gui())
    try: sys.exit(_run_cli())
    except Exception as exc:
        print('ERROR:',exc,file=sys.stderr);sys.exit(1)
