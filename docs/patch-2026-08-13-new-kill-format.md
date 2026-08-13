# 2026-08-13 game update: the kill packet moved

The August 13 patch replaced the 5-name fixed-offset kill packet. The logger
records nothing because the old detector demands 5 names at registry offsets
and the new record has a different shape. NOT an encryption change: names are
plaintext UTF-16LE, same servers (game 8889, chat 8884), framing intact.

## Evidence

- Pre-patch war capture (2026-07-26, 178MB of port-8889 payload): ZERO
  occurrences of marker a70000ae1c.
- Post-patch war capture (2026-08-13_20-00-48, ~2min of war setup phase):
  30 markers, of which two double-block records = two kill events:
    (KiRiELs / MyGuEL17)      x  (BanSinOfGreed / NetaxOwlee)   @4742643
    (Rezo / Mikion)           x  (CIarissa / Amourah)           @4864538
- Same structure seen in open-world captures and in diag NEAR-MISS lines
  (e.g. Hank4/BettercallHank4 x Kawandax/Zelldario during the war window).

## New structure

Identity block (repeats; most messages carry ONE, kill records carry TWO):

    a7 00 00 ae 1c                       marker (5 bytes)
    <UTF-16LE char name, 56-byte field>
    <u32, values seen 01/02/04>          meaning unknown, not needed
    <UTF-16LE family name, 56-byte field>
    <ids/state>

Kill record signature, byte-identical in both observed kill events,
immediately preceding the FIRST identity block:

    2e 03 01 00 03 01 02 00 00  a7 00 00 ae 1c

Gap between the two identity blocks varies (208 and 400 bytes observed), so
FIXED-OFFSET PARSING CANNOT WORK for the second block. The parser must scan:
find the signature, take block 1, scan forward (<= ~600B) for the second
marker, take block 2.

## What the new record does NOT carry

- No guild name (old packet had one at offset 30). Candidate replacement: the
  nearby-player packets carry char/family/guild trios (names at +73/+66
  spacing in the same stream) - maintain a family->guild cache during capture
  and join at emit, or emit empty guild and let CoGM resolve server-side
  (it already resolves guilds by family for recaps).
- No obvious self-reference: the old packet was self-centric (my kill / my
  death); the new one looks observer-style (any kill in range, like the
  on-screen feed). If confirmed, the war parser gets MORE data than before.

## Unknowns needing one ground-truth kill (capture + who-killed-whom)

1. Block order: killer first or victim first (feed convention says killer
   first: "[Soggy] forcefully slaughtered [Whenever]").
2. Whether a direction/kill byte exists, or direction is purely block order.
3. The record opcode for the sniffer's candidate filter (region before the
   signature: 560c... / 4f06... constants seen in both records).

## Fix plan

Logger v1.32: marker-scan detector behind the existing framing (framing is
unchanged - the sniffer's candidates already contain these records). Registry
gains a "signature" entry so future moves of the constant ship server-side.
Ship via the release process: tag first, verify installer, then main.

Analysis scripts: scratchpad pcap_hunt.py / marker_dump.py / record_extract.py
(session 2026-08-13). Captures in ~/Downloads.
