"""Warscore correlation tool.

Given a pcap captured during a BDO node war, plus a CSV of warscore numbers
typed in from the post-war Enhanced Warscore screen, this scans every 4-byte
uint32 window in every BDO packet subtype and looks for offsets where the
captured value matches the screenshot value within a tolerance.

If a (subtype_identifier, byte_offset) consistently matches "damage_dealt"
across multiple players, that's almost certainly the field offset.

Usage:
    python tools/correlate_warscore.py path/to/capture.pcap path/to/warscore.csv

The warscore.csv format (header required):
    family_name,damage_dealt,healing_done,cc_count,kills,deaths

Numbers must be integers (the screenshot displays integers). Players whose
family name was never seen in the kill packets are skipped.

Output:
    tools/out/warscore_offsets.csv
        identifier,byte_offset,endian,field,confidence,sample

confidence = (players_matched / players_with_packets), 0.0 to 1.0.
A confidence >= 0.7 across 5+ players is a strong hit.

No game memory is read. No client modification. Only the user's own pcap.
"""

from __future__ import annotations

import argparse
import csv
import struct
import sys
from collections import defaultdict
from pathlib import Path

try:
    from scapy.all import rdpcap
except ImportError:
    print("scapy not installed. run: pip install scapy", file=sys.stderr)
    sys.exit(1)

# Reuse the subtype extractor from the sibling analyzer
sys.path.insert(0, str(Path(__file__).parent))
from analyze_combat_packet import (  # noqa: E402
    extract_subtype_packets,
    PACKET_LEN,
)


# Match tolerance: the screenshot is taken seconds after the last update,
# so the captured value can lag the displayed value by a few percent.
TOLERANCE_FRACTION = 0.05
TOLERANCE_FLOOR = 50  # absolute floor for small numbers (a few CCs etc.)


def load_warscore_csv(path: Path) -> list[dict]:
    rows = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append(
                    {
                        "family_name": row["family_name"].strip(),
                        "damage_dealt": int(row.get("damage_dealt", "0") or 0),
                        "healing_done": int(row.get("healing_done", "0") or 0),
                        "cc_count": int(row.get("cc_count", "0") or 0),
                        "kills": int(row.get("kills", "0") or 0),
                        "deaths": int(row.get("deaths", "0") or 0),
                    }
                )
            except (ValueError, KeyError) as exc:
                print(f"skipping malformed row: {row} ({exc})", file=sys.stderr)
    return rows


def family_name_in_packet(packet: bytes, name: str) -> bool:
    """Heuristic: family names live as UTF-16-LE around the kill packet's
    name offsets (201, 263). For sibling subtypes the offset is unknown,
    so we just substring-search for the UTF-16 encoding anywhere."""
    needle = name.encode("utf-16-le")
    return needle in packet


def values_close(captured: int, target: int) -> bool:
    if target == 0:
        return captured == 0
    tolerance = max(target * TOLERANCE_FRACTION, TOLERANCE_FLOOR)
    return abs(captured - target) <= tolerance


def scan_offsets_for_player(
    packets: list[bytes],
    target_value: int,
) -> list[tuple[int, str]]:
    """For one player's packets and one warscore field, find all
    (offset, endian) pairs where most packets contain a uint32 close to target.

    A pair counts as a hit if at least 60% of the player's packets agree.
    """
    if not packets or target_value == 0:
        return []

    hits: list[tuple[int, str]] = []
    for endian in ("<I", ">I"):
        endian_label = "LE" if endian == "<I" else "BE"
        for off in range(0, PACKET_LEN - 4):
            matches = 0
            for p in packets:
                if len(p) < off + 4:
                    continue
                try:
                    val = struct.unpack(endian, p[off : off + 4])[0]
                except struct.error:
                    continue
                if values_close(val, target_value):
                    matches += 1
            if matches >= max(1, int(len(packets) * 0.6)):
                hits.append((off, endian_label))
    return hits


def correlate(by_identifier: dict, warscore: list[dict]) -> list[dict]:
    """For each subtype, for each warscore field, find offsets where
    the captured uint32 matches across multiple players."""

    # offset_field_hits[ident][(field, offset, endian)] = set(player_names_matched)
    offset_field_hits: dict = defaultdict(lambda: defaultdict(set))
    # players_with_packets[ident] = set(player_names)
    players_with_packets: dict = defaultdict(set)

    fields = ("damage_dealt", "healing_done", "cc_count", "kills", "deaths")

    for ident, packets in by_identifier.items():
        if not packets or ident.endswith("_short"):
            continue
        for player in warscore:
            name = player["family_name"]
            if not name:
                continue
            player_packets = [p for p in packets if family_name_in_packet(p, name)]
            if not player_packets:
                continue
            players_with_packets[ident].add(name)
            for field in fields:
                target = player[field]
                if target <= 0:
                    continue
                for off, endian in scan_offsets_for_player(player_packets, target):
                    offset_field_hits[ident][(field, off, endian)].add(name)

    rows = []
    for ident, hit_map in offset_field_hits.items():
        total_players = len(players_with_packets[ident]) or 1
        for (field, off, endian), names in hit_map.items():
            confidence = len(names) / total_players
            rows.append(
                {
                    "identifier": ident,
                    "byte_offset": off,
                    "endian": endian,
                    "field": field,
                    "confidence": round(confidence, 3),
                    "matched_players": len(names),
                    "total_players": total_players,
                    "sample_names": ", ".join(sorted(names)[:5]),
                }
            )
    rows.sort(key=lambda r: (-r["confidence"], -r["matched_players"]))
    return rows


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap", help="path to .pcap captured during a BDO node war")
    ap.add_argument("warscore", help="CSV of warscore numbers typed from screenshot")
    ap.add_argument("--outdir", default="tools/out", help="where to write reports")
    args = ap.parse_args()

    pcap_path = Path(args.pcap)
    warscore_path = Path(args.warscore)
    if not pcap_path.exists():
        print(f"pcap not found: {pcap_path}", file=sys.stderr)
        sys.exit(2)
    if not warscore_path.exists():
        print(f"warscore CSV not found: {warscore_path}", file=sys.stderr)
        sys.exit(2)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"loading warscore from {warscore_path} ...")
    warscore = load_warscore_csv(warscore_path)
    print(f"  {len(warscore)} players")

    print(f"extracting subtypes from {pcap_path} ...")
    by_identifier = extract_subtype_packets(pcap_path)
    print(f"  {len(by_identifier)} subtype identifiers")

    print("correlating warscore values against packet uint32 windows ...")
    rows = correlate(by_identifier, warscore)
    out_path = outdir / "warscore_offsets.csv"
    write_csv(out_path, rows)
    print(f"wrote {len(rows)} candidate offset/field pairs to {out_path}")

    if rows:
        print()
        print("=== top 10 candidates ===")
        for r in rows[:10]:
            print(
                f"  {r['identifier']} off={r['byte_offset']:>3} {r['endian']} "
                f"field={r['field']:>13} conf={r['confidence']:.2f} "
                f"({r['matched_players']}/{r['total_players']})"
            )


if __name__ == "__main__":
    main()
