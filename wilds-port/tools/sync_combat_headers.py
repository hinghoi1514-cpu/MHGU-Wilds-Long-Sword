"""Synchronize generated tables into the existing integration source tree."""
from pathlib import Path
import re,shutil,sys
from portkit import PROJECT
out=Path(sys.argv[1]);c=PROJECT.parent/'integration/c'
for name in ['wilds_root_data.h','wilds_combat_data.h']:shutil.copy2(out/name,c/name)
p=c/'world_events.h';s=p.read_text();s=re.sub(r'\s*\{9\d\du,\d+u,\d+u,0u,0u\},','',s)
s=s.replace('static const WClip world_clips[]={','static const WClip world_clips[]={\n'+(out/'world_clip_entries.txt').read_text())
p.write_text(s)
print('Updated generated headers and dedicated clip records')
