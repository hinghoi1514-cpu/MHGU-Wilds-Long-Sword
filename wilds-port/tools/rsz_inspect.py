"""Strict local USER/RSZ inspection with a matching REasy type dump.

No files in the game installation are modified. Objects remain references rather
than being recursively expanded, which preserves cycles and original indices.
"""
import argparse
import json
import struct
from pathlib import Path


def read_user(path,schema):
    b=Path(path).read_bytes()
    assert b[:4]==b'USR\0'
    r=struct.unpack_from('<Q',b,32)[0]
    assert b[r:r+4]==b'RSZ\0'
    version,object_count,count,userdata_count=struct.unpack_from('<4I',b,r+4)
    info,data,ud=struct.unpack_from('<3Q',b,r+24)
    externals={struct.unpack_from('<I',b,r+ud+16*i)[0] for i in range(userdata_count)}
    roots=list(struct.unpack_from('<'+'I'*object_count,b,r+48))
    pos=r+data
    objects=[None]*count
    def align(n):
        nonlocal pos
        pos=(pos+n-1)//n*n
    def read_field(f):
        nonlocal pos
        align(f['align'])
        kind=f['type']
        if kind=='RuntimeType':
            n=struct.unpack_from('<I',b,pos)[0];pos+=4
            if n>len(b)-pos:raise ValueError(f'Invalid runtime type string {n}')
            value=b[pos:pos+n].decode('utf-8').rstrip('\0');pos+=n
            return value
        if kind in ('String','Resource'):
            n=struct.unpack_from('<I',b,pos)[0];pos+=4
            if n>len(b)//2:raise ValueError(f'Invalid string length {n} at {pos:x}')
            value=b[pos:pos+2*n].decode('utf-16le').rstrip('\0');pos+=2*n
            return value
        fmt={'Object':'I','UserData':'I','U32':'I','S32':'i','F32':'f','Bool':'?','U8':'B','S8':'b',
             'U16':'H','S16':'h','U64':'Q','S64':'q','F64':'d'}.get(kind)
        if fmt:
            value=struct.unpack_from('<'+fmt,b,pos)[0]
        else:
            value=b[pos:pos+f['size']].hex()
        pos+=f['size']
        return value
    for i in range(1,count):
        typeid,crc=struct.unpack_from('<2I',b,r+info+8*i)
        typeinfo=schema.get(f'{typeid:x}')
        if typeinfo is None:raise ValueError(f'Unknown type {typeid:x}')
        if int(typeinfo['crc'],16)!=crc:raise ValueError(f'CRC mismatch: {typeinfo["name"]}')
        if i in externals:
            objects[i]={'type':typeinfo['name'],'external':True}
            continue
        start=pos
        fields={}
        for f in typeinfo['fields']:
            try:
                if f['array']:
                    align(4)
                    n=struct.unpack_from('<I',b,pos)[0];pos+=4
                    if n>100000:raise ValueError(f'Invalid array count {n}')
                    fields[f['name']]=[read_field(f) for _ in range(n)]
                else:
                    fields[f['name']]=read_field(f)
            except Exception as e:
                raise ValueError(f'instance {i} {typeinfo["name"]} field {f["name"]}, pos {pos:x}: {e}') from e
        objects[i]={'type':typeinfo['name'],'offset':start,'fields':fields}
    return {'source':str(path),'roots':roots,'objects':objects,'data_end':pos,'file_size':len(b)}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('source')
    p.add_argument('--schema',required=True)
    p.add_argument('--out',required=True)
    a=p.parse_args()
    schema=json.loads(Path(a.schema).read_text(encoding='utf-8'))
    result=read_user(a.source,schema)
    Path(a.out).parent.mkdir(parents=True,exist_ok=True)
    Path(a.out).write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print({k:v for k,v in result.items() if k!='objects'})
