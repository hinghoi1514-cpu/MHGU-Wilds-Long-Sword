"""Offline stick-figure previews. These do not substitute for in-game checks."""
from pathlib import Path
import argparse
import json
import math

import numpy as np
from PIL import Image, ImageDraw
from portkit import *


def frames_for(offset, count=32):
    mot=Motlist()
    clip=mot.clip(offset)
    gu,remap=gu_skeleton()
    rev={r:v for v,r in remap.items()}
    result=[]
    for f in range(count):
        sw=source_pose(mot,clip,f*clip['frames']/(count-1))
        desired={v:matrixq(sw[mot.by_name[name]]) for v,name in BODY_MAP.items()}
        rots={}
        for v,r in remap.items():
            p=gu[r].parent
            pq=desired[rev[p]] if p is not None else np.array([0,0,0,1])
            rots[r]=qmul(qinv(pq),desired[v])
        gw=world_pose(gu,rots,{remap[0]:sw[mot.by_name['Hip']][:3,3]*100})
        src={name:sw[mot.by_name[name]][:3,3]*100 for name in BODY_MAP.values()}
        dst={BODY_MAP[v]:gw[r][:3,3] for v,r in remap.items()}
        result.append((src,dst))
    return clip,result


def draw_pose(draw, points, cx, label, yaw=-.55):
    origin=points['Hip'].copy()
    origin[1]=0
    scale=1.4
    def project(p):
        p=p-origin
        return (cx+scale*(math.cos(yaw)*p[0]+math.sin(yaw)*p[2]),
                322-scale*(p[1]+.18*p[2]))
    # Same semantic skeleton used for both panels, with collapsed spine/neck.
    parent={1:0,2:1,3:2,4:3,5:2,6:5,7:6,8:7,9:2,10:9,11:10,12:11,14:0,15:14,16:15,17:0,18:17,19:18}
    for v,p in parent.items():
        color='#65caff' if BODY_MAP[v].startswith('L_') else '#f6b660' if BODY_MAP[v].startswith('R_') else '#e4e9f3'
        a,b=project(points[BODY_MAP[v]]),project(points[BODY_MAP[p]])
        draw.line([a,b],fill=color,width=4)
        draw.ellipse([a[0]-3,a[1]-3,a[0]+3,a[1]+3],fill=color)
    draw.text((cx-110,48),label,fill='#d3d9e4')
    draw.line([(cx-125,326),(cx+125,326)],fill='#404c61',width=1)


def preview(offset,out):
    clip,poses=frames_for(offset)
    frames=[]
    for i,(source,target) in enumerate(poses):
        image=Image.new('RGB',(640,380),'#101725')
        draw=ImageDraw.Draw(image)
        draw.text((20,14),f"{clip['name']} | source content unconfirmed | {i+1}/{len(poses)}",fill='white')
        draw_pose(draw,source,165,'Wilds decoded skeleton')
        draw_pose(draw,target,480,'GU proposed retarget')
        draw.text((20,350),'Offline skeleton preview; weapon/grip and game validation pending.',fill='#97a7be')
        frames.append(image)
    out=Path(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    frames[0].save(out,save_all=True,append_images=frames[1:],duration=round(clip['frames']/60/len(frames)*1000),loop=0)
    sheet=Image.new('RGB',(640*4,380*2))
    for j,i in enumerate(np.linspace(0,len(frames)-1,8).astype(int)):
        sheet.paste(frames[i],((j%4)*640,(j//4)*380))
    sheet.save(out.with_suffix('.png'))
    print(out)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--offset',type=lambda x:int(x,0),default=0x131c70)
    p.add_argument('--out',required=True)
    a=p.parse_args()
    preview(a.offset,a.out)
