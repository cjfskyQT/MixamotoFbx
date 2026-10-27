"""JSON worker entry point. No downloaded MA script is executed."""
import json
import os
from pathlib import Path
import sys
import traceback

def main(request_path=None, response_path=None):
    from _bootstrap import import_module
    request = Path(request_path or os.environ['CAPOEIRA_JOB_REQUEST'])
    response = Path(response_path or os.environ['CAPOEIRA_JOB_RESPONSE'])
    initialized = False
    try:
        import maya.standalone
        maya.standalone.initialize(name='python')
        initialized = True
        dispatch = import_module('maya_engine').dispatch
        result = dispatch(json.loads(request.read_text(encoding='utf-8')))
        response.write_text(json.dumps({'ok': True, 'result': result}, ensure_ascii=False), encoding='utf-8')
    except BaseException as exc:
        response.write_text(json.dumps({'ok': False, 'error': str(exc), 'traceback': traceback.format_exc()}, ensure_ascii=False), encoding='utf-8')
    finally:
        if initialized:
            maya.standalone.uninitialize()


if __name__ == '__main__':
    main(*sys.argv[1:3])
