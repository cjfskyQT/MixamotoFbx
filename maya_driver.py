"""Maya controller discovery and checked commandPort transport."""
import json
from pathlib import Path
import socket
import uuid
from .adv_scan import ADV_FKIK_SWITCHES, scan_ma_file, filter_by_kind
from .bone_matcher import auto_match_controllers
from .scene_safety import validate_scene

ADV_IKFK_CONTROLLERS=ADV_FKIK_SWITCHES
ADV_IKFK_ATTRIBUTE='FKIKBlend'
ADV_IKFK_ATTRIBUTE_ALT='IKFKBlend'


def list_adv_controllers_from_ma(path):
    return [c.name for c in scan_ma_file(path) if c.kind in ('FK', 'Root')]


def auto_map_from_ma(path):
    from .bone_matcher import MIXAMO_CANONICAL
    return auto_match_controllers(MIXAMO_CANONICAL,list_adv_controllers_from_ma(path)).active_targets()


def load_ma_file(path):
    validate_scene(path)
    from maya import cmds
    if cmds.file(query=True,modified=True):
        raise RuntimeError('请先保存当前 Maya 场景。')
    cmds.file(path,open=True,force=True,executeScriptNodes=False)


def apply_in_process(source_anim,mapping,*,start_frame=None,end_frame=None,scene_fps=None,force_fk=True,contact_compensation=True,legs_ik=True,**kwargs):
    from .maya_engine import retarget
    if not source_anim.source_path: raise ValueError('缺少源 FBX 路径。')
    return retarget(source_anim.source_path,mapping=mapping,mode='maya',start=start_frame,
                    stop=end_frame,force_fk_enabled=force_fk,contact_compensation=contact_compensation,legs_ik=legs_ik)['bones_driven']


def run_remote_job(payload,host='127.0.0.1',port=7001):
    if host not in ('127.0.0.1','localhost','::1'):
        raise ValueError('仅支持本机 Maya commandPort。')
    if payload.get('target'): validate_scene(payload['target'])
    marker='CAPOEIRA_RESULT_'+uuid.uuid4().hex
    root=str(Path(__file__).resolve().parent)
    bootstrap=str(Path(root)/'_bootstrap.py')
    script=("import sys,json,traceback,importlib.util\n"
            +f"sys.path.insert(0,{root!r})\n"
            +"try:\n"
            +f"    spec=importlib.util.spec_from_file_location('_capoeira_remote_bootstrap',{bootstrap!r})\n"
            +"    loader=importlib.util.module_from_spec(spec)\n"
            +"    spec.loader.exec_module(loader)\n"
            +"    retarget=loader.import_module('maya_engine').retarget\n"
            +f"    result=retarget(**json.loads({json.dumps(payload)!r}))\n"
            +f"    print({marker!r}+json.dumps(dict(ok=True,result=result)))\n"
            +"except Exception as exc:\n"
            +f"    print({marker!r}+json.dumps(dict(ok=False,error=str(exc))))\n")
    command='exec('+repr(script)+')\n'
    try:
        with socket.create_connection((host,port),timeout=10) as conn:
            conn.settimeout(120)
            conn.sendall(command.encode('utf-8'))
            received=''
            while True:
                chunk=conn.recv(65536)
                if not chunk: raise RuntimeError('Maya 在返回执行结果前断开连接。')
                received+=chunk.decode('utf-8',errors='replace')
                if marker in received:
                    tail=received.split(marker,1)[1]
                    try: data,_=json.JSONDecoder().raw_decode(tail)
                    except ValueError: continue
                    if not data.get('ok'): raise RuntimeError(data.get('error','Maya bake failed'))
                    if not data['result'].get('key_count'): raise RuntimeError('Maya 未生成动画关键帧。')
                    return data['result']
    except (OSError,socket.timeout) as exc:
        raise RuntimeError('连接 Maya 失败或未收到确认。请在 Maya 中开启 Python commandPort（端口 '+str(port)+'，echoOutput=True）。原始错误：'+str(exc)) from exc


def apply_via_command_port(source_anim,mapping,*,host='127.0.0.1',port=7001,start_frame=None,end_frame=None,force_fk=True,contact_compensation=True,legs_ik=True):
    if not source_anim.source_path: raise ValueError('缺少源 FBX 路径。')
    return run_remote_job(dict(source=source_anim.source_path,mapping=mapping,mode='maya',
                               start=start_frame,stop=end_frame,force_fk_enabled=force_fk,contact_compensation=contact_compensation,legs_ik=legs_ik),host,port)['bones_driven']
