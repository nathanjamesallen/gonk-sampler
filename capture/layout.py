"""
Pad layout: which sampler note each sound lands on.

Default is the General MIDI drum map inside the 16 pads of a 4x4 controller
(Ableton Drum Rack / most pad grids start at note 36 = C1, bottom-left):

    48 tom hi-mid   49 crash      50 tom high    51 ride
    44 pedal hat    45 tom low    46 open hat    47 tom low-mid
    40 snare 2      41 floor tom  42 closed hat  43 floor tom hi
    36 KICK         37 rim        38 SNARE       39 CLAP

A sound can name its slot outright ("slot": 46). Otherwise its role picks the
first free slot from that role's list, so a second kick or snare falls to the
alternate slots and percussion fills the gaps GM leaves (37, 40, 44).
"""

GRID = list(range(36, 52))

GM_NAMES = {36: "kick", 37: "rim", 38: "snare", 39: "clap", 40: "snare 2", 41: "floor tom",
            42: "closed hat", 43: "floor tom hi", 44: "pedal hat", 45: "tom low", 46: "open hat",
            47: "tom low-mid", 48: "tom hi-mid", 49: "crash", 50: "tom high", 51: "ride"}

ROLE_SLOTS = {
    "kick":     [36, 40, 37],
    "snare":    [38, 40, 37],
    "clap":     [39, 37, 40],
    "rim":      [37, 40, 39],
    "hat":      [42, 44, 46],
    "pedalhat": [44, 42],
    "openhat":  [46, 44],
    "tom":      [41, 43, 45, 47, 48, 50],
    "cymbal":   [49, 51],
    "ride":     [51, 49],
    "bell":     [37, 44, 40, 51],
    "perc":     [37, 40, 44, 43, 47, 48, 50],
    "zap":      [40, 37, 44, 50, 48],
}


def assign_slots(sounds, taken=None):
    """Fill in s['slot'] for every sound (in place). Explicit slots first, then
    roles in list order. Raises if two sounds ask for the same explicit slot."""
    used = dict(taken or {})
    for s in sounds:
        if s.get("slot") is not None:
            if s["slot"] in used:
                raise ValueError(f"slot {s['slot']} wanted by both {used[s['slot']]} and {s['name']}")
            used[s["slot"]] = s["name"]
    for s in sounds:
        if s.get("slot") is not None:
            continue
        prefs = ROLE_SLOTS.get(s.get("role", "perc"), ROLE_SLOTS["perc"])
        free = [n for n in prefs if n not in used] or [n for n in GRID if n not in used] \
            or [n for n in range(52, 128) if n not in used]
        s["slot"] = free[0]
        used[s["slot"]] = s["name"]
    return sounds
