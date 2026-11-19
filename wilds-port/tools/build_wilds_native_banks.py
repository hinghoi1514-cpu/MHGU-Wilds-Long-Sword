"""Replace the traced ordinary Long Sword swing and hit slots in GU weapon banks."""
from pathlib import Path
from portkit import arc_replace, read_arc, unpack

def native_slots(entries):
    bank = next(e.data() for e in entries if e.kind == 0x15d782fb)
    requests = next(e.data() for e in entries if e.kind == 0x1bcc4966)
    offset = 32
    paths = {}
    for _ in range(unpack('I', bank, 12)[0]):
        end = bank.index(0, offset)
        path = bank[offset:end].decode()
        for key in bank[end+76:end+79]:
            if key:
                paths[key] = path
        offset = end + 81
    slots = {}
    for i in range(unpack('I', requests, 8)[0]):
        row = requests[52+i*132:52+(i+1)*132]
        slots[unpack('H', row)[0]] = paths.get(row[2])
    return slots

def build_native_banks(game_banks, private_archive, output):
    waves = {e.name.rsplit('\\',1)[-1]: e.data() for e in read_arc(private_archive)[1][:-2]}
    folder = Path(game_banks)
    for source in sorted(folder.glob('[0-9]*.arc')):
        slots = native_slots(read_arc(source)[1])
        assert slots[1].endswith('_01') and slots[2].endswith('_02') and slots[7] == slots[2], source
        arc_replace(source, Path(output)/source.name,
                    {slots[1]: waves['release_cut1'], slots[2]: waves['wind_2_3']})
    source = folder/'mat.arc'
    mapping = {'swo_hit_iron': 'release_blade0', 'two_layer_whoosh_fast': 'wind_2_1',
               'two_layer_whoosh_slow': 'wind_0_2'}
    replacements = {e.name: waves[mapping[e.name.rsplit('\\',1)[-1]]] for e in read_arc(source)[1]
                    if e.name.rsplit('\\',1)[-1] in mapping}
    assert len(replacements) == len(mapping)
    arc_replace(source, Path(output)/source.name, replacements)

if __name__ == '__main__':
    import sys
    build_native_banks(*sys.argv[1:])
