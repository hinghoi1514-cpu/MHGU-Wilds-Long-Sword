"""Local Wilds MOT932 -> MHGU LMT67 research tools.

Format references, not a claim of game validation:
  PredatorCZ/RevilLib src/mtf_lmt/codecs.cpp (14-bit signed quaternion keys)
  DemonDrugd/MHGU-blenderImporter (MOD skeleton/remap)
  seifhassine/REasy resources/data/motion/tracks.json (MOT932 encodings)
All quaternions here are xyzw, matrices act on column vectors.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
GAME = Path(os.environ.get("MHGU_DATA_ROOT", PROJECT.parent / "local-data"))
MOT = PROJECT / 'assets/wilds/natives/stm/motion/player/weapon/wp03/wp03_00/wp03_00.motlist.992'
MODEL = GAME / "_work/mhgu-mot/plmodel/003_player_mod_f_pl001_f_body001.bin"
BASE_ARC = GAME / "_work/akumi-src/package/Wilds-LS-World/romfs/nativeNX/arc/player/quest/w07.arc"
ANIM_ENTRY = r"player\mot\w07\w07"


def unpack(fmt, buf, offset=0):
    return struct.unpack_from('<' + fmt, buf, offset)


def qnorm(q):
    q = np.asarray(q, dtype=float)
    length = np.linalg.norm(q)
    if not np.isfinite(length) or length < 1e-10:
        raise ValueError(f'Invalid quaternion {q}')
    return q / length


def qmul(a, b):
    av, bv = np.asarray(a[:3]), np.asarray(b[:3])
    return np.r_[a[3]*bv + b[3]*av + np.cross(av, bv), a[3]*b[3]-np.dot(av, bv)]


def qinv(q):
    return np.asarray(q) * [-1, -1, -1, 1] / np.dot(q, q)


def qmatrix(q):
    x, y, z, w = qnorm(q)
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def matrixq(m):
    # Stable conversion including 180-degree rotations.
    m = np.asarray(m)[:3, :3]
    vals, vecs = np.linalg.eigh(np.array([
        [m[0,0]-m[1,1]-m[2,2], m[1,0]+m[0,1], m[2,0]+m[0,2], m[2,1]-m[1,2]],
        [m[1,0]+m[0,1], m[1,1]-m[0,0]-m[2,2], m[2,1]+m[1,2], m[0,2]-m[2,0]],
        [m[2,0]+m[0,2], m[2,1]+m[1,2], m[2,2]-m[0,0]-m[1,1], m[1,0]-m[0,1]],
        [m[2,1]-m[1,2], m[0,2]-m[2,0], m[1,0]-m[0,1], m.trace()]]))
    q = vecs[:, np.argmax(vals)]
    return qnorm(q if q[3] >= 0 else -q)


def qangle(a, b):
    return math.degrees(2*math.acos(float(np.clip(abs(np.dot(qnorm(a), qnorm(b))), -1, 1))))


def nlerp(a, b, t):
    return qnorm((1-t)*a + t*(b if np.dot(a, b) >= 0 else -b))


def decode_q14(data):
    word = int.from_bytes(data, 'little')
    raw = [(word >> shift) & 0x3fff for shift in (42, 28, 14, 0)]
    return np.array([(v if v <= 8191 else v-16383)*4/16383 for v in raw]), word >> 56


def encode_q14(q, duration=1):
    q = qnorm(q)
    if not 0 <= duration <= 255:
        raise ValueError('Key duration is an unsigned byte')
    # Match the game's signed-magnitude-like range; negative zero has two codes.
    raw = [round(abs(float(v))*16383/4) for v in q]
    raw = [16383-v if c < 0 else v for v, c in zip(raw, q)]
    word = int(duration) << 56
    for v, shift in zip(raw, (42, 28, 14, 0)):
        word |= v << shift
    return word.to_bytes(8, 'little')


@dataclass
class ArcEntry:
    name: str
    kind: int
    flags: int
    compressed: bytes

    def data(self):
        return zlib.decompress(self.compressed)


def read_arc(path):
    b = Path(path).read_bytes()
    assert b[:4] == b'ARC\0'
    entries = []
    for i in range(unpack('H', b, 6)[0]):
        off = 12+i*80
        name = b[off:off+64].split(b'\0')[0].decode('ascii')
        kind, size, flags, at = unpack('4I', b, off+64)
        entry = ArcEntry(name, kind, flags, b[at:at+size])
        assert len(entry.data()) == flags & 0x1fffffff, name
        entries.append(entry)
    return b[:12], entries


def arc_replace(source, dest, replacements):
    header, entries = read_arc(source)
    known = {e.name for e in entries}
    assert replacements.keys() <= known
    table = bytearray(header + bytes(80*len(entries)))
    for i, e in enumerate(entries):
        payload = zlib.compress(replacements[e.name]) if e.name in replacements else e.compressed
        flags = ((e.flags & 0xe0000000) | len(replacements[e.name])) if e.name in replacements else e.flags
        table += bytes((-len(table)) % 16)
        offset = len(table)
        struct.pack_into('<64s4I', table, 12+i*80, e.name.encode(), e.kind, len(payload), flags, offset)
        table += payload
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(table)
    _, actual = read_arc(dest)
    assert all(e.data() == replacements.get(e.name, entries[i].data()) for i, e in enumerate(actual))


@dataclass
class LmtTrack:
    header: bytes
    codec: int
    usage: int
    joint: int
    bone: int
    reference: np.ndarray
    data: bytes


class Lmt:
    def __init__(self, data):
        self.data = bytes(data)
        assert self.data[:4] == b'LMT\0'
        self.version, self.count = unpack('HH', self.data, 4)
        assert self.version == 67, self.version

    def descriptor(self, slot):
        # Slot zero is empty in this asset; it is not an extra reserved word.
        off = unpack('I', self.data, 8+slot*4)[0]
        if not off:
            raise ValueError(f'Empty slot {slot}')
        return self.data[off:off+64]

    def tracks(self, slot):
        offset, count, frames = unpack('3I', self.descriptor(slot))
        tracks = []
        for i in range(count):
            hdr = self.data[offset+36*i:offset+36*(i+1)]
            codec, usage, joint, bone = unpack('4B', hdr)
            size, at = unpack('2I', hdr, 8)
            tracks.append(LmtTrack(hdr, codec, usage, joint, bone, np.array(unpack('4f', hdr, 16)), self.data[at:at+size]))
        return frames, tracks

    def replace(self, slot, rotations, positions=None, frames=None, template=None):
        template = slot if template is None else template
        old_frames, tracks = self.tracks(template)
        frames = frames or old_frames
        dest = bytearray(self.data)
        dest += bytes((-len(dest)) % 16)
        desc_off = len(dest)
        desc = bytearray(self.descriptor(template))
        table_off = desc_off+64
        struct.pack_into('<3I', desc, 0, table_off, len(tracks), frames)
        dest += desc + bytes(len(tracks)*36)
        for i, track in enumerate(tracks):
            hdr = bytearray(track.header)
            block = track.data
            if track.bone in rotations and track.usage in (0, 3):
                values = rotations[track.bone]
                assert len(values) == frames
                block = b''.join(encode_q14(q, 1 if n+1 < frames else 0) for n, q in enumerate(values))
                hdr[0] = 6
                struct.pack_into('<4f', hdr, 16, *qnorm(values[0]))
                struct.pack_into('<I', hdr, 32, 0)
            if positions is not None and track.bone in positions and track.usage in (1, 4):
                values = positions[track.bone]
                assert len(values) == frames
                block = b''.join(struct.pack('<3fI', *v[:3], 1 if n+1 < frames else 0) for n, v in enumerate(values))
                hdr[0] = 3
                struct.pack_into('<4f', hdr, 16, *values[0][:3], 0)
                struct.pack_into('<I', hdr, 32, 0)
            dest += bytes((-len(dest)) % 16)
            struct.pack_into('<2I', hdr, 8, len(block), len(dest) if block else 0)
            # Quantization bound pointers are retained only for untouched data.
            dest[table_off+36*i:table_off+36*(i+1)] = hdr
            dest += block
        struct.pack_into('<I', dest, 8+slot*4, desc_off)
        return bytes(dest)


@dataclass
class Bone:
    name: str
    parent: int | None
    translation: np.ndarray
    rotation: np.ndarray


def gu_skeleton(path=MODEL):
    b = Path(path).read_bytes()
    assert b[:4] == b'MOD\0'
    count = unpack('H', b, 6)[0]
    at = unpack('I', b, 36)[0]
    remap = b[at+152*count:at+152*count+256]
    reverse = {real: virtual for virtual, real in reversed(list(enumerate(remap))) if real < count}
    bones = []
    for i in range(count):
        _, parent, _, _ = unpack('4B', b, at+24*i)
        m = np.array(unpack('16f', b, at+24*count+64*i)).reshape(4, 4).T
        assert np.allclose(m[3], [0, 0, 0, 1], atol=1e-4)
        bones.append(Bone(f'gu_{reverse[i]}', None if parent == 255 else parent, m[:3, 3], matrixq(m)))
    return bones, {virtual: real for virtual, real in enumerate(remap) if real < count}


def world_pose(bones, rotations=None, positions=None):
    rotations = {} if rotations is None else rotations
    positions = {} if positions is None else positions
    worlds = {}
    def visit(i):
        if i in worlds:
            return worlds[i]
        bone = bones[i]
        m = np.eye(4)
        m[:3, :3] = qmatrix(rotations.get(i, bone.rotation))
        m[:3, 3] = positions.get(i, bone.translation)
        worlds[i] = m if bone.parent is None else visit(bone.parent) @ m
        return worlds[i]
    for i in range(len(bones)):
        visit(i)
    return worlds


def encoding_specs():
    families = json.loads((PROJECT/'reference/reasy_tracks.json').read_text())['families']
    def inherited(name):
        f = families[name]
        result = inherited(f['extends']) if 'extends' in f else {}
        for kind, specs in f.get('keys', {}).items():
            result.setdefault(kind, {}).update(specs)
        return result
    return inherited('mot932')


class Motlist:
    def __init__(self, path=MOT):
        self.data = Path(path).read_bytes()
        self.specs = encoding_specs()
        # Bone base transforms are embedded in the first clip. They describe its
        # reference pose, which must not be silently treated as a T pose.
        self.first = self.data.find(b'mot ')-4
        base = self.first
        sk = base+unpack('Q', self.data, base+16)[0]
        records, count = unpack('2Q', self.data, sk)
        self.bones = []
        for i in range(count):
            at = base+records+80*i
            name, parent = unpack('2Q', self.data, at)
            p = (parent-records)//80 if parent else None
            if p is not None:
                assert 0 <= p < count and (parent-records) % 80 == 0
            self.bones.append(Bone(self.string(base+name), p,
                                   np.array(unpack('3f', self.data, at+32)),
                                   np.array(unpack('4f', self.data, at+48))))
        self.by_name = {b.name: i for i, b in enumerate(self.bones)}

    def string(self, at):
        end = at
        while self.data[end:end+2] != b'\0\0':
            end += 2
        return self.data[at:end].decode('utf-16le')

    def curve(self, base, at, kind):
        flags, count, fi, data, params = unpack('5I', self.data, at)
        solver = flags & 0xfff
        assert solver == {'translation': 242, 'rotation': 274, 'scale': 242}[kind], (kind, hex(flags))
        frame_type = (flags >> 20) & 15
        fmt = {2: 'B', 4: 'H', 5: 'I'}[frame_type]
        times = unpack(fmt*count, self.data, base+fi) if fi else (0,)*count
        assert count and (fi or count == 1)
        assert all(a <= b for a, b in zip(times, times[1:]))
        spec = self.specs['quaternion' if kind == 'rotation' else 'vector3'][str((flags >> 12) & 255)]
        pars = unpack('f'*spec.get('params', 0), self.data, base+params)
        values = []
        for i in range(count):
            raw = self.data[base+data+i*spec['size']:base+data+(i+1)*spec['size']]
            bits = spec.get('bits', 0)
            decode = spec['decode']
            if bits:
                word = int.from_bytes(raw, spec.get('endian', 'little'))
                nc = 3 if decode == 'packed' else (2 if decode == 'pair' else 1)
                coded = [(word >> (bits*j)) & ((1 << bits)-1) for j in range(nc)]
            else:
                coded = list(unpack('f'*(len(raw)//4), raw))
            if decode == 'full':
                v = coded
            elif decode == 'packed':
                half = len(pars)//2
                v = [coded[j]*(pars[j]/((1 << bits)-1))+pars[half+j] for j in range(len(coded))]
            elif decode == 'uniform':
                v = [(coded[0]*pars[0]/((1 << bits)-1)+pars[1]) if bits else coded[0]]*3
            else:
                axes = [spec['axis']] if decode == 'axis' else spec['axes']
                bg = pars[(1 if decode == 'axis' else 2):] if bits else pars
                v = list(bg[:3]) if len(bg) >= 3 else [0., 0., 0.]
                offsets = [bg[a] for a in axes] if len(bg) >= 3 else (list(bg) or [0.]*len(axes))
                for j, axis in enumerate(axes):
                    v[axis] = coded[j]*pars[j]/((1 << bits)-1)+offsets[j] if bits else coded[j]
            if kind == 'rotation':
                if len(v) == 3:
                    rest = 1-sum(x*x for x in v)
                    if rest < -0.015:
                        raise ValueError(f'Invalid decoded xyz, clip={base:x}, curve={at:x}: {v}')
                    v.append(math.sqrt(max(0, rest)))
                # Quantization can cause a small norm error; report raw norms
                # separately rather than using normalized length as a decoder test.
            if not np.all(np.isfinite(v)):
                raise ValueError('Nonfinite decoded key')
            values.append(v)
        return Curve(times, values, kind, flags)

    def clip(self, base):
        b = self.data
        assert unpack('I', b, base)[0] == 932
        assert b[base+4:base+8] == b'mot '
        channels = {}
        at = base+unpack('Q', b, base+24)[0]
        for i in range(unpack('H', b, base+0x72)[0]):
            joint, transforms, weight, bhash, curves = unpack('HBBII', b, at+12*i)
            assert not transforms & ~7
            cs = {}
            for bit, kind in enumerate(('translation', 'rotation', 'scale')):
                if transforms & (1 << bit):
                    cs[kind] = self.curve(base, base+curves, kind)
                    curves += 20
            channels[joint] = cs
        return {'offset': base, 'name': self.string(base+unpack('Q', b, base+0x58)[0]),
                'frames': unpack('f', b, base+0x60)[0], 'channels': channels}

    def inventory(self):
        # Only exact MOT932 headers, subsequently parsed with all bounds checks.
        needle=struct.pack('<I',932)+b'mot '
        result=[]
        at=self.data.find(needle)
        while at>=0:
            result.append(at)
            at=self.data.find(needle,at+8)
        return result


class Curve:
    def __init__(self, times, values, kind, flags):
        self.times, self.values, self.kind, self.flags = times, np.array(values), kind, flags

    def sample(self, time):
        i = max(0, bisect.bisect_right(self.times, time)-1)
        if i >= len(self.times)-1:
            v = self.values[-1]
            return qnorm(v) if self.kind == 'rotation' else v
        t = np.clip((time-self.times[i])/(self.times[i+1]-self.times[i]), 0, 1)
        a, b = self.values[i:i+2]
        return nlerp(a, b, t) if self.kind == 'rotation' else (1-t)*a+t*b


def source_pose(mot, clip, time):
    rotations, positions = {}, {}
    for joint, channels in clip['channels'].items():
        if 'rotation' in channels:
            rotations[joint] = channels['rotation'].sample(time)
        if 'translation' in channels:
            positions[joint] = channels['translation'].sample(time)
    return world_pose(mot.bones, rotations, positions)


# GU IDs come from the MOD remap, not sampled joint numbers. Torso collapses
# extra source spine/neck joints using world orientations. The map is a first
# anatomical proposal; animation identity and grip still need visual validation.
BODY_MAP = {0:'Hip', 1:'Spine_0', 2:'Spine_2', 3:'Neck_0', 4:'Head',
            5:'L_Shoulder', 6:'L_UpperArm', 7:'L_Forearm', 8:'L_Hand',
            9:'R_Shoulder', 10:'R_UpperArm', 11:'R_Forearm', 12:'R_Hand',
            14:'L_Thigh', 15:'L_Shin', 16:'L_Foot',
            17:'R_Thigh', 18:'R_Shin', 19:'R_Foot'}


def make_probe(out, mode, offset=0x131c70, slot=244):
    out=Path(out)
    out.mkdir(parents=True,exist_ok=True)
    _,entries=read_arc(BASE_ARC)
    original=next(e.data() for e in entries if e.name==ANIM_ENTRY)
    lmt=Lmt(original)
    frames,tracks=lmt.tracks(slot)
    gu,remap=gu_skeleton()
    rev={r:v for v,r in remap.items()}
    rotations={v:[] for v in remap}
    rotations[255]=[]
    positions={0:[],255:[]}
    mot=Motlist()
    clip=mot.clip(offset)
    if not clip['name'].startswith('wp03_'):
        raise ValueError(f"Refusing non-longsword source {clip['name']}")
    # Target bind local axes in this model are identity (within float noise),
    # matching the source's +X arms / -Y legs convention. Preserve the source's
    # absolute world orientations, then derive each target local from its real
    # target parent. Never reset a move to its own first frame.
    for f in range(frames):
        if mode=='identity':
            for v in rotations:
                rotations[v].append([0,0,0,1])
            positions[0].append([0,95,0])
        else:
            time=f*clip['frames']/max(1,frames-1)
            sw=source_pose(mot,clip,time)
            desired={v:matrixq(sw[mot.by_name[name]]) for v,name in BODY_MAP.items()}
            for v,r in remap.items():
                p=gu[r].parent
                pq=desired[rev[p]] if p is not None else np.array([0,0,0,1])
                rotations[v].append(qnorm(qmul(qinv(pq),desired[v])))
            rotations[255].append([0,0,0,1])
            positions[0].append(sw[mot.by_name['Hip']][:3,3]*100)
        positions[255].append([0,0,0])
    result=lmt.replace(slot,rotations,positions)
    (out/f'{mode}.lmt').write_bytes(result)
    arc_replace(BASE_ARC,out/f'{mode}.arc',{ANIM_ENTRY:result})
    verify=Lmt(result)
    max_error=0
    for t in verify.tracks(slot)[1]:
        if t.codec==6 and t.bone in rotations:
            for f in range(frames):
                q,_=decode_q14(t.data[f*8:(f+1)*8])
                max_error=max(max_error,qangle(q,rotations[t.bone][f]))
    manifest={'status':'experimental, not accepted as a finished move',
              'mode':mode,'source_clip':clip['name'],'source_offset':hex(offset),
              'slot':slot,'frames':frames,'max_encoded_rotation_error_degrees':max_error,
              'base_arc_sha256':hashlib.sha256(BASE_ARC.read_bytes()).hexdigest(),
              'result_arc_sha256':hashlib.sha256((out/f'{mode}.arc').read_bytes()).hexdigest(),
              'mapping':BODY_MAP}
    (out/f'{mode}.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in manifest.items() if k!='mapping'},indent=2))


def inspect(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    mot = Motlist()
    bones, remap = gu_skeleton()
    gu = world_pose(bones)
    report = {'gu': [{'id': v, 'real': r, 'parent': bones[r].parent,
                       'position': gu[r][:3, 3].tolist(), 'local_rotation': bones[r].rotation.tolist()}
                      for v, r in remap.items()],
              'source_bones': [{'id': i, 'name': b.name, 'parent': b.parent,
                                 'translation': b.translation.tolist(), 'rotation': b.rotation.tolist()}
                                for i, b in enumerate(mot.bones)]}
    (out/'skeletons.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    _, entries = read_arc(BASE_ARC)
    lmt = next(e.data() for e in entries if e.name == ANIM_ENTRY)
    (out/'baseline.lmt').write_bytes(lmt)
    for slot in [144, 147, 148, 157, 238, 244]:
        count, tracks = Lmt(lmt).tracks(slot)
        print('SLOT', slot, 'frames', count, 'tracks', [(t.codec, t.usage, t.bone) for t in tracks])
    for offset in [mot.first, 0x131c70]:
        c = mot.clip(offset)
        raw_norms = [np.linalg.norm(q) for ch in c['channels'].values() if 'rotation' in ch for q in ch['rotation'].values]
        print('CLIP', c['name'], c['frames'], 'channels', len(c['channels']), 'rotation norm range', min(raw_norms), max(raw_norms))
    print('GU skeleton:')
    for item in report['gu']:
        print(item['id'], item['real'], 'parent-real', item['parent'], 'xyz', np.round(item['position'], 2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['inspect','probe'])
    p.add_argument('--out', required=True)
    p.add_argument('--mode',choices=['identity','source'],default='identity')
    p.add_argument('--offset',type=lambda x:int(x,0),default=0x131c70)
    p.add_argument('--slot',type=int,default=244)
    args = p.parse_args()
    if args.command=='inspect':
        inspect(args.out)
    else:
        make_probe(args.out,args.mode,args.offset,args.slot)
