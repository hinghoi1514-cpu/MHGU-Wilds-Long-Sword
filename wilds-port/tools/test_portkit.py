"""Format regression tests using original game data and independent decoders."""
import ast
import math
from pathlib import Path
import random
import struct
import tempfile
import unittest

import numpy as np
from portkit import *


class Formats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.entries = read_arc(BASE_ARC)
        cls.original = next(e.data() for e in cls.entries if e.name == ANIM_ENTRY)
        cls.lmt = Lmt(cls.original)

    def test_slots_match_plugin(self):
        for slot, frames in {144:131, 147:201, 148:159, 157:131, 244:117}.items():
            self.assertEqual(self.lmt.tracks(slot)[0], frames)

    def test_q14_known_keys_and_timings(self):
        max_norm_error = 0
        count = 0
        for slot in [144,147,148,157,238,244]:
            frames, tracks = self.lmt.tracks(slot)
            for t in tracks:
                if t.codec != 6:
                    continue
                total = 0
                for offset in range(0,len(t.data),8):
                    data=t.data[offset:offset+8]
                    q, dt=decode_q14(data)
                    max_norm_error=max(max_norm_error,abs(np.linalg.norm(q)-1))
                    q2, dt2=decode_q14(encode_q14(q,dt))
                    self.assertLess(qangle(q,q2),0.08)
                    self.assertEqual(dt,dt2)
                    total+=dt
                    count+=1
                self.assertIn(total,[frames-1,frames],(slot,t.bone,total,frames))
        self.assertLess(max_norm_error,0.003)
        print(f'Original data: {count} packed keys, max raw norm error {max_norm_error:.6g}')

    def test_pack_identity_has_w_and_time(self):
        # x=y=z=0, w=1 rounds to 4096, high byte is the frame delta.
        self.assertEqual(encode_q14([0,0,0,1],1).hex(),'0010000000000001')
        q,dt=decode_q14(bytes.fromhex('0010000000000001'))
        self.assertEqual(dt,1)
        self.assertLess(qangle(q,[0,0,0,1]),1e-6)

    def test_translation_key_time_matches_original_and_round_trip(self):
        frames,tracks=self.lmt.tracks(144)
        original=next(t for t in tracks if t.codec==3 and t.bone==255)
        durations=[unpack('I',original.data,i+12)[0] for i in range(0,len(original.data),16)]
        self.assertIn(sum(durations),[frames-1,frames])
        values=[[float(i),95.+i*.1,-float(i)] for i in range(frames)]
        rebuilt=Lmt(self.lmt.replace(144,{},positions={0:values,255:values}))
        for t in rebuilt.tracks(144)[1]:
            if t.bone not in [0,255] or t.usage not in [1,4]:continue
            decoded=[unpack('3fI',t.data,i) for i in range(0,len(t.data),16)]
            self.assertEqual(sum(v[3] for v in decoded),frames-1)
            self.assertTrue(all(v[3]==1 for v in decoded[:-1]))
            self.assertEqual(decoded[-1][3],0)
            self.assertTrue(np.allclose([v[:3] for v in decoded],values))

    def test_random_quat_precision(self):
        rng=np.random.default_rng(1932)
        for q in rng.normal(size=(1000,4)):
            got,_=decode_q14(encode_q14(q))
            self.assertLess(qangle(q,got),0.06)
            self.assertTrue(np.allclose(qmatrix(q),qmatrix(matrixq(qmatrix(q))),atol=1e-10))

    def test_rebuild_preserves_reference_and_other_slots(self):
        frames, tracks=self.lmt.tracks(244)
        values={0:np.tile([0.,0.,0.,1.],(frames,1))}
        rebuilt=Lmt(self.lmt.replace(244,values))
        self.assertEqual(rebuilt.tracks(244)[0],117)
        for slot in range(self.lmt.count):
            if not unpack('I',self.original,8+4*slot)[0]:
                continue
            if slot!=244:
                self.assertEqual(self.lmt.descriptor(slot),rebuilt.descriptor(slot))
        for old,new in zip(tracks,rebuilt.tracks(244)[1]):
            if old.bone!=0 or old.usage!=0:
                self.assertEqual(old.data,new.data)
                self.assertTrue(np.array_equal(old.reference,new.reference))

    def test_skeleton_parent_map_is_from_model(self):
        bones,remap=gu_skeleton()
        reverse={real:virtual for virtual,real in remap.items()}
        expected={1:0,2:1,3:2,4:3,5:2,6:5,7:6,8:7,9:2,10:9,11:10,12:11,14:0,15:14,16:15,17:0,18:17,19:18}
        for child,parent in expected.items():
            self.assertEqual(reverse[bones[remap[child]].parent],parent)

    def test_source_static_tracks_and_curve_types(self):
        mot=Motlist()
        for off in [mot.first,0x131c70]:
            clip=mot.clip(off)
            self.assertEqual(len(clip['channels']),72)
            self.assertIn('translation',clip['channels'][3]) # COG, not a rotation
            self.assertNotIn('rotation',clip['channels'][3])
            self.assertIn('rotation',clip['channels'][1]) # Constant ground-angle key
            self.assertTrue(np.all(np.isfinite(source_pose(mot,clip,30)[20])))

    def test_decode_entire_correct_longsword_library(self):
        mot=Motlist()
        clips=[mot.clip(off) for off in mot.inventory()]
        self.assertEqual(len(clips),290)
        self.assertTrue(all(c['name'].startswith('wp03_') for c in clips))

    def test_arc_preserves_every_other_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'probe.arc'
            new=self.lmt.replace(244,{})
            arc_replace(BASE_ARC,target,{ANIM_ENTRY:new})
            _,after=read_arc(target)
            for a,b in zip(self.entries,after):
                if a.name!=ANIM_ENTRY:
                    self.assertEqual(a.compressed,b.compressed)


if __name__=='__main__':
    unittest.main(verbosity=2)
