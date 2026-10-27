"""Run isolated Maya jobs without requiring the standalone FBX Python SDK."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import sys


def find_mayapy():
    override = os.environ.get('MAYAPY')
    if override and Path(override).is_file():
        return override
    if Path(sys.executable).name.lower() == 'mayapy.exe':
        return sys.executable
    base = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Autodesk'
    choices = sorted(base.glob('Maya*/bin/mayapy.exe'), reverse=True)
    if choices:
        return str(choices[0])
    raise RuntimeError('找不到 Maya。请安装 Maya 或设置 MAYAPY 为 mayapy.exe 的完整路径。')


def run_job(payload):
    worker = Path(__file__).with_name('maya_worker.py')
    scratch = Path(os.environ.get('CAPOEIRA_TEMP_DIR', str(Path(tempfile.gettempdir()) / 'capoeira_retarget_jobs'))).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='capoeira_', dir=str(scratch)) as folder:
        folder = Path(folder)
        request, response = folder/'request.json', folder/'response.json'
        request.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        env = os.environ.copy()
        env.update(MAYA_APP_DIR=str(folder/'profile'), MAYA_SKIP_USERSETUP_PY='1',
                   MAYA_SKIP_USERSETUP_MEL='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
                   PYTHONPATH=str(worker.parent)+os.pathsep+env.get('PYTHONPATH',''),
                   CAPOEIRA_JOB_REQUEST=str(request),CAPOEIRA_JOB_RESPONSE=str(response))
        (folder/'profile').mkdir()
        # Maya on Windows can misdecode non-ASCII command-line paths. Import
        # through Unicode environment paths instead of passing script filenames.
        command="from _bootstrap import import_module; import_module('maya_worker').main()"
        result = subprocess.run([find_mayapy(), '-c', command],
                                env=env, capture_output=True, text=True,
                                encoding='utf-8', errors='replace',
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if not response.exists():
            raise RuntimeError('Maya 任务未完成：\n' + (result.stderr or result.stdout)[-3000:])
        data = json.loads(response.read_text(encoding='utf-8'))
        if not data.get('ok'):
            raise RuntimeError(data.get('error', 'Maya job failed'))
        return data['result']
