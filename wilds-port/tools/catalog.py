"""A bounded visual catalog of decoded longsword clips, with source IDs."""
import argparse
import json
import math
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np
from portkit import *


def catalog(out,lo,hi):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    mot=Motlist()
    selected=[]
    for off in mot.inventory():
        c=mot.clip(off)
        num=int(c['name'].split('_')[2])
        if lo<=num<=hi:selected.append(c)
    selected.sort(key=lambda c:int(c['name'].split('_')[2]))
    parent={1:0,2:1,3:2,4:3,5:2,6:5,7:6,8:7,9:2,10:9,11:10,12:11,14:0,15:14,16:15,17:0,18:17,19:18}
    for page in range((len(selected)+15)//16):
        group=selected[page*16:(page+1)*16]
        canvas=Image.new('RGB',(1440,180*len(group)),'#101725');draw=ImageDraw.Draw(canvas)
        for row,c in enumerate(group):
            poses=[source_pose(mot,c,t) for t in np.linspace(0,c['frames'],6)]
            points=[{v:pose[mot.by_name[name]][:3,3]*100 for v,name in BODY_MAP.items()} for pose in poses]
            values=[]
            for pts in points:
                origin=pts[0]*[1,0,1]
                values.extend([(p-origin) for p in pts.values()])
            ylow=min(p[1] for p in values);yhi=max(p[1] for p in values)
            scale=min(1.15,140/max(1,yhi-ylow))
            for col,pts in enumerate(points):
                origin=pts[0]*[1,0,1]
                def project(p):
                    p=p-origin
                    return (240*col+120+scale*(.86*p[0]-.5*p[2]),180*row+164-scale*(p[1]-ylow))
                for child,p in parent.items():
                    color='#65caff' if BODY_MAP[child].startswith('L_') else '#f6b660' if BODY_MAP[child].startswith('R_') else '#e4e9f3'
                    draw.line([project(pts[child]),project(pts[p])],fill=color,width=3)
                draw.text((240*col+6,180*row+4),f"{c['name']} | {c['frames']:g}f" if col==0 else f"{col}/5",fill='white')
            draw.line([(0,(row+1)*180-1),(1440,(row+1)*180-1)],fill='#3a465a')
        canvas.save(out/f'catalog-{lo}-{hi}-{page}.png')
    (out/f'catalog-{lo}-{hi}.json').write_text(json.dumps([{k:v for k,v in c.items() if k!='channels'} for c in selected],indent=2))
    print('Cataloged',len(selected),'clips')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--lo',type=int,default=150);p.add_argument('--hi',type=int,default=295)
    a=p.parse_args();catalog(a.out,a.lo,a.hi)
