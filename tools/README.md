# Tools

Offline analysis helpers. Nothing here runs at logger runtime. Nothing
touches the game client. Everything operates on pcap files the user
captured themselves.

## `analyze_combat_packet.py`

Takes a `.pcap` captured during a BDO fight and builds a map of what
lives in each byte offset of the 300-byte combat packet. Used to find
unmapped fields (coordinates, skill IDs, class IDs, timestamps) that
the shipping parser ignores.

### Install

```
pip install scapy
```

### Capture a pcap

Run the shipping logger with the `-r` / `--record` flag so it writes a
`.pcap` alongside the normal `.log`:

```
logger.py -r -o my-fight.log
```

Or use Wireshark directly. Capture for the duration of a node war or
siege, filter nothing (we need the raw TCP stream).

### Run the analyzer

```
python tools/analyze_combat_packet.py my-fight.pcap
```

### Output

Reports land in `tools/out/`:

- `bytes.csv` — per-byte distribution across every packet. Stable
  low-variance offsets are good candidates for enums / flags /
  class IDs.
- `floats.csv` — 4-byte windows that decode as plausible float32
  coordinates across most packets, ranked by plausibility.
- `enums.csv` — byte offsets where 2 to 16 discrete values appear,
  sorted by how small the value set is. Good hunting ground for class
  IDs and crit flags.
- `monotonic.csv` — 4-byte uint32 windows that increase over time.
  Session ticks and server timestamps live here.
- `raw_samples.txt` — first 10 packets dumped as annotated hex so the
  known field layout can be eyeballed for drift.

### What to look for

The shipping parser only reads bytes 6-69 (guild), 35-ish (kill flag),
201-264 (player one), 263+ (player two). Roughly offsets **70 through
200** are never touched. That's the dead zone. Anything interesting in
the report that lives in that range is an unmapped field.

Hit the top float-candidate triples first: three adjacent float32
windows where all three score high plausibility are almost certainly
an x/y/z position triple.
