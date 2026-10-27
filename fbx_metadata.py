"""Read FBX frame-rate metadata without loading the animation or an SDK."""
from pathlib import Path
import re
import struct


def metadata(path):
    data=Path(path).read_bytes()
    values={}
    models={}
    if data.startswith(b'Kaydara FBX Binary'):
        version=struct.unpack_from('<I',data,23)[0]
        fmt,size=('<QQQB',25) if version>=7500 else ('<IIIB',13)
        def prop(pos):
            kind=chr(data[pos]);pos+=1
            scalar={'Y':'<h','C':'<?','I':'<i','F':'<f','D':'<d','L':'<q'}
            if kind in scalar:
                fmt=scalar[kind];return struct.unpack_from(fmt,data,pos)[0],pos+struct.calcsize(fmt)
            if kind in ('S','R'):
                count=struct.unpack_from('<I',data,pos)[0];pos+=4
                return data[pos:pos+count].decode('utf-8','replace'),pos+count
            raise ValueError('Unexpected FBX metadata property: '+kind)
        def walk(pos,global_settings=False,model=None):
            end,count,plen,nlen=struct.unpack_from(fmt,data,pos)
            if not end:return pos+size
            name=data[pos+size:pos+size+nlen].decode('utf-8')
            start=pos+size+nlen
            if name not in ('Objects','GlobalSettings','Model','Properties70','P'):
                return end
            if name=='GlobalSettings':global_settings=True
            props=[];pos=start
            for _ in range(count):
                value,pos=prop(pos);props.append(value)
            if name=='Model':
                model=props[1].split('\x00')[0]
                models.setdefault(model,{})
            if name=='P' and props:
                if global_settings:values[props[0]]=props[4:]
                elif model is not None:models[model][props[0]]=props[4:]
            while pos<end-size:pos=walk(pos,global_settings,model)
            return end
        pos=27
        while pos+size<len(data):
            if not struct.unpack_from(fmt,data,pos)[0]:break
            pos=walk(pos)
    else:
        text=data.decode('utf-8','replace')
        for name in ('TimeMode','CustomFrameRate'):
            match=re.search(r'P:\s*"'+name+r'"[^\r\n]*,\s*(-?[\d.]+)\s*(?:\r?\n|$)',text)
            if match:values[name]=[float(match.group(1))]
    return dict(settings=values,models=models)


def frame_rate(path):
    values={k:v[0] for k,v in metadata(path)['settings'].items() if v}
    mode=int(values.get('TimeMode',6))
    if mode==14 and values.get('CustomFrameRate',0)>0:return float(values['CustomFrameRate'])
    return {1:120.,2:100.,3:60.,4:50.,5:48.,6:30.,7:30.,8:29.97,9:29.97,
            10:25.,11:24.,12:1000.,13:23.976,15:96.,16:72.,17:59.94}.get(mode,30.)
