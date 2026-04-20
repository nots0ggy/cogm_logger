"""Combat packet dead zone analyzer.

Takes a pcap file captured during BDO node war traffic and profiles
every byte position inside the 300-byte combat packet. For each byte
offset it reports:

  - value distribution (unique count, most common values)
  - whether it looks like a stable integer enum (small discrete set)
  - whether consecutive 4-byte windows look like plausible float32
    coordinates (values in a sensible BDO-map range)
  - whether consecutive 4-byte windows look like monotonic uint32
    counters (session ticks, timestamps)
  - whether consecutive 2-byte / 4-byte windows look like plausible IDs
    (class IDs are typically small uint8/uint16 values, skill IDs are
    uint32 values in a bounded range)

The known fields (guild at 6, kill flag at ~35, player_one at 201,
player_two at 263) are highlighted so we can visually confirm the map
before trusting the dead-zone findings.

Usage:
    python tools/analyze_combat_packet.py path/to/capture.pcap
    python tools/analyze_combat_packet.py path/to/capture.pcap --csv out.csv

Output:
    tools/out/bytes.csv          one row per byte offset with heuristics
    tools/out/floats.csv         candidate float32 windows sorted by plausibility
    tools/out/enums.csv          candidate enum columns (stable small set)
    tools/out/raw_samples.txt    first 10 packets as hex, human-readable

No game memory is read, no client is modified, only the raw pcap file
that the user captured themselves.
"""

from __future__ import annotations

import argparse
import csv
import struct
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, pstdev

try:
    from scapy.all import rdpcap
except ImportError:
    print("scapy not installed. run: pip install scapy", file=sys.stderr)
    sys.exit(1)


# Same magic bytes the shipping parser uses. First pass sticks to the
# known kill/death packet so the known-field map is stable. A later
# subtype classifier will widen the net.
COMBAT_IDENTIFIER = bytes.fromhex("630100af12")

# Known field offsets from the shipping parser. Byte offsets, not hex.
KNOWN_FIELDS = {
    0: ("magic", 5),
    6: ("guild_utf16", 64),
    35: ("kill_flag_nibble", 1),  # hex offset 141 -> byte 70.5 -> practically byte 35 nibble
    201: ("player_one_utf16", 64),
    263: ("player_two_utf16", 64),
}

PACKET_LEN = 300
DEAD_ZONE_START = 35
DEAD_ZONE_END = 201


def extract_combat_packets(pcap_path: Path) -> list[bytes]:
    """Return the 300-byte combat payload slices from a pcap."""
    packets = []
    caps = rdpcap(str(pcap_path))
    carry = b""
    for pkt in caps:
        if not pkt.haslayer("TCP"):
            continue
        tcp = pkt["TCP"]
        if not hasattr(tcp.payload, "load"):
            continue
        payload = carry + bytes(tcp.payload)
        while True:
            idx = payload.find(COMBAT_IDENTIFIER)
            if idx < 0:
                # keep last 4 bytes in case identifier is split across packet boundary
                carry = payload[-4:] if len(payload) > 4 else payload
                break
            if idx + PACKET_LEN > len(payload):
                carry = payload[idx:]
                break
            window = payload[idx : idx + PACKET_LEN]
            packets.append(window)
            payload = payload[idx + PACKET_LEN :]
    return packets


def byte_profile(packets: list[bytes]) -> list[dict]:
    """Per-byte-offset profile across all packets."""
    if not packets:
        return []
    rows = []
    n = len(packets)
    for off in range(PACKET_LEN):
        column = [p[off] for p in packets if off < len(p)]
        c = Counter(column)
        top = c.most_common(5)
        uniq = len(c)
        # entropy-ish: how concentrated is the most common value
        dominant_frac = top[0][1] / n if top else 0
        known = None
        for k_off, (k_name, k_len) in KNOWN_FIELDS.items():
            if k_off <= off < k_off + k_len:
                known = k_name
                break
        rows.append({
            "offset": off,
            "known_field": known or "",
            "unique_values": uniq,
            "dominant_value": f"0x{top[0][0]:02x}" if top else "",
            "dominant_pct": round(dominant_frac * 100, 1),
            "top_5": ",".join(f"0x{v:02x}({cnt})" for v, cnt in top),
            "min": min(column) if column else 0,
            "max": max(column) if column else 0,
            "mean": round(mean(column), 2) if column else 0,
            "stdev": round(pstdev(column), 2) if len(column) > 1 else 0,
        })
    return rows


def scan_float_windows(packets: list[bytes]) -> list[dict]:
    """Score every 4-byte window as a candidate float32 coordinate.

    BDO world coordinates are typically in the range roughly
    [-500000, 500000]. Anything outside that plausible band is rejected.
    A high fraction of valid-looking finite values means it's probably
    a real float field.
    """
    results = []
    if not packets:
        return results
    # Only scan the dead zone plus a buffer, since known fields are strings
    for off in range(DEAD_ZONE_START, DEAD_ZONE_END - 3):
        le_vals = []
        be_vals = []
        for p in packets:
            if off + 4 > len(p):
                continue
            chunk = p[off : off + 4]
            try:
                le_vals.append(struct.unpack("<f", chunk)[0])
                be_vals.append(struct.unpack(">f", chunk)[0])
            except Exception:
                pass
        for endian, vals in (("LE", le_vals), ("BE", be_vals)):
            if not vals:
                continue
            plausible = [v for v in vals if _plausible_coord(v)]
            frac = len(plausible) / len(vals)
            if frac < 0.5:
                continue
            results.append({
                "offset": off,
                "endian": endian,
                "plausible_fraction": round(frac, 3),
                "mean": round(mean(plausible), 2) if plausible else 0,
                "min": round(min(plausible), 2) if plausible else 0,
                "max": round(max(plausible), 2) if plausible else 0,
                "stdev": round(pstdev(plausible), 2) if len(plausible) > 1 else 0,
                "sample_values": ",".join(f"{v:.1f}" for v in plausible[:5]),
            })
    results.sort(key=lambda r: -r["plausible_fraction"])
    return results


def _plausible_coord(v: float) -> bool:
    """True if a float looks like a BDO map coordinate.

    BDO map coords sit roughly in the hundreds-of-thousands range on
    the big continent map. We accept a wide window to catch any game
    position axis without being too permissive.
    """
    import math
    if not math.isfinite(v):
        return False
    av = abs(v)
    if av == 0.0:
        return False
    if av > 1_000_000:
        return False
    if 0.0001 < av < 100:
        # small floats like buff intensities are possible too, keep them
        return True
    return 100 <= av <= 1_000_000


def scan_enum_columns(rows: list[dict]) -> list[dict]:
    """Byte offsets where the set of observed values is small and stable.

    Class IDs, event type enums, and flags live here.
    """
    candidates = []
    for r in rows:
        if r["known_field"]:
            continue
        if r["unique_values"] <= 1:
            continue
        # 2-16 distinct values across many packets = probable enum
        if 2 <= r["unique_values"] <= 16 and r["offset"] >= DEAD_ZONE_START and r["offset"] < DEAD_ZONE_END:
            candidates.append({
                "offset": r["offset"],
                "unique_values": r["unique_values"],
                "dominant_pct": r["dominant_pct"],
                "top_5": r["top_5"],
            })
    candidates.sort(key=lambda r: r["unique_values"])
    return candidates


def scan_monotonic_windows(packets: list[bytes]) -> list[dict]:
    """4-byte windows that look like monotonically increasing counters.

    Server ticks and session timestamps always increase across the fight.
    """
    results = []
    if len(packets) < 10:
        return results
    for off in range(DEAD_ZONE_START, DEAD_ZONE_END - 3):
        for endian in ("<I", ">I"):
            vals = []
            for p in packets:
                if off + 4 > len(p):
                    continue
                try:
                    vals.append(struct.unpack(endian, p[off : off + 4])[0])
                except Exception:
                    pass
            if len(vals) < 10:
                continue
            # count monotonic-increase fraction
            inc = sum(1 for i in range(1, len(vals)) if vals[i] >= vals[i - 1])
            frac = inc / (len(vals) - 1)
            # also require the range to be plausibly "ms since something"
            # not just all zeros or all maxed
            span = max(vals) - min(vals)
            if frac >= 0.85 and span > 100 and min(vals) != max(vals):
                results.append({
                    "offset": off,
                    "endian": "LE" if endian == "<I" else "BE",
                    "monotonic_fraction": round(frac, 3),
                    "min": min(vals),
                    "max": max(vals),
                    "span": span,
                })
    results.sort(key=lambda r: -r["monotonic_fraction"])
    return results


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("(no rows)\n")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_hex_samples(path: Path, packets: list[bytes], count: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, p in enumerate(packets[:count]):
        lines.append(f"=== packet {i + 1} ({len(p)} bytes) ===")
        # 16 bytes per row, annotated
        for row_off in range(0, len(p), 16):
            row = p[row_off : row_off + 16]
            hex_part = " ".join(f"{b:02x}" for b in row)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
            tag = ""
            for k_off, (k_name, k_len) in KNOWN_FIELDS.items():
                if row_off <= k_off < row_off + 16 or k_off <= row_off < k_off + k_len:
                    tag = f"  <-- {k_name}"
                    break
            lines.append(f"  {row_off:4d}  {hex_part:<47}  |{ascii_part}|{tag}")
        lines.append("")
    path.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap", help="path to .pcap file captured during BDO combat")
    ap.add_argument("--outdir", default="tools/out", help="where to write reports")
    args = ap.parse_args()

    pcap_path = Path(args.pcap)
    if not pcap_path.exists():
        print(f"pcap not found: {pcap_path}", file=sys.stderr)
        sys.exit(2)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"loading {pcap_path} ...")
    packets = extract_combat_packets(pcap_path)
    print(f"extracted {len(packets)} combat packets")
    if not packets:
        print("no combat packets found. confirm BDO was running and the pcap covers a fight.")
        sys.exit(3)

    print("profiling per-byte distribution ...")
    rows = byte_profile(packets)
    write_csv(outdir / "bytes.csv", rows)

    print("scanning float32 windows ...")
    floats = scan_float_windows(packets)
    write_csv(outdir / "floats.csv", floats)

    print("scanning enum columns ...")
    enums = scan_enum_columns(rows)
    write_csv(outdir / "enums.csv", enums)

    print("scanning monotonic uint32 windows ...")
    monos = scan_monotonic_windows(packets)
    write_csv(outdir / "monotonic.csv", monos)

    print("dumping 10 raw packet samples ...")
    write_hex_samples(outdir / "raw_samples.txt", packets)

    print()
    print(f"done. reports written to {outdir}/")
    print()
    print("=== quick summary ===")
    print(f"  packets:           {len(packets)}")
    print(f"  float candidates:  {len(floats)}  (top: offset {floats[0]['offset']} {floats[0]['endian']} frac={floats[0]['plausible_fraction']})" if floats else "  float candidates:  0")
    print(f"  enum candidates:   {len(enums)}")
    print(f"  monotonic windows: {len(monos)}  (top: offset {monos[0]['offset']} {monos[0]['endian']} span={monos[0]['span']})" if monos else "  monotonic windows: 0")


if __name__ == "__main__":
    main()
