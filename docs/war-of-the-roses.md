# War of the Roses support (research notes)

Status: **not implemented.** This is a writeup of what we understand so far so we
can pick it up later. War of the Roses (WoR) is a separate BDO game mode with a
different kill-feed packet format than node war. The current parser mistracks it.

## The problem

The node-war parser (`logger/src/options/live_capture.py`) assumes the node-war
combat packet: identifier `630100af12`, a fixed 5-name layout, names at fixed
offsets. WoR is different, so on a WoR fight the logger:

- grabs internal string-table keys (`LUA_CHANNELCHAT_`, `ROOM_DESC_30`,
  `ROOM_NAME_37`) **as if they were player names**, and
- only keeps packets that happen to expose exactly 5 name-like strings
  (`if len(names) == 5`), so it caught **5 of dozens** of events, all garbled.

Example of the garbled output it produced (`session-1781475471805.log` →
saved as `14.06.2026`):

```
[05:26:18] Poxont died to ROOM_NAME_37 from LUA_CHANNELCHAT_ (ROOM_DESC_30,LUA_CHANNELCHAT_)
[05:26:18] 52 died to ,JTheM from LUA_CHANNELCHAT_ (ROOM_DESC_30,rinder)
```

The in-game feed for the same fight actually reads (see the screenshots):

```
[Splyte] killed [Elvega] from the [O'dyllita Army].
[Llynwen] was slain by [LookAtThat] of the [O'dyllita Army].
```

## What WoR packets actually look like

Decoded from the raw session (`docs/samples/wor-raw-session-1781475471805.log`,
the 8-field CSV the engine writes: `identifier,time,name+pos x5,600-hex`). All
strings are **UTF-16LE** (byte, `00`, byte, `00`, ...). Offsets below are
**hex-character positions** into the 600-char window, the same coordinate space
the node-war parser uses.

Combat packets carry identifier **`6101004c00`** and are **localized chat
messages**: a string-table template key followed by the real names as arguments.

```
event 1: id=6101004c00
  @6    TEMPLATE  LUA_CHANNELCHAT_ROOM_DESC_30
  @186  name      PeanutGrinder
  @328  name      TheManic
  @452  name      Kaanao
event 2: id=6101004c00
  @6    TEMPLATE  LUA_CHANNELCHAT_ROOM_DESC_30
  @192  name      Jinu_Xaja
  @334  name      EighteenDragons
  @458  name      EighteenSlices
event 3: id=6101004c00
  @6    TEMPLATE  LUA_CHANNELCHAT_ROOM_DESC_30
  @198  name      NOPVPLOL
  @340  name      Uicyl
  @464  name      Sthiss
```

### Key findings

1. **Identifier:** `6101004c00` for WoR combat. (The node-war regex
   `[56][0-9a-f]0100[0-9a-f]{4}` also matches this, which is why these packets
   get picked up at all, just parsed wrong.)
2. **Template key encodes the message type → direction.** Two variants seen:
   `LUA_CHANNELCHAT_ROOM_DESC_30` and `LUA_CHANNELCHAT_ROOM_NAME_37`. These are
   almost certainly the two kill-feed formats ("X killed Y" vs "X was slain by
   Y"), i.e. **direction lives in the template key, not a byte offset.** If so,
   WoR direction could be *more* reliable than node war (no fragile kill nibble).
3. **Three name args per combat packet**, not five. Likely killer / victim /
   army-or-third-party, but the exact semantics are **unconfirmed** (see open
   questions). The names are real BDO family names.
4. **Offsets are not fixed.** Across events 1→2→3 every name slot drifts +6 hex
   in lockstep (186→192→198, 328→334→340, 452→458→464). That points to one
   variable-length field early in the packet (a counter, timestamp, or the army
   string) that pushes the args. So a WoR parser must **scan-and-skip the
   template keys and read the next UTF-16 runs**, not hard-code offsets.
5. **Other identifiers appear in the same stream.** Event 4 had identifier
   `6901002e00` with 4 names (`LiFAH`, `MITUTU`, `LORDPRETTYFLACKOJORDIE`,
   `CarudKryze`) and parsed more like node-war format ("... has killed ..."). May
   be a different message subtype, a node-war packet bleeding in, or a different
   event class. Needs identification.
6. Event 0 was anomalous (only one real name, `Poxont`, at @526, templates at odd
   offsets) — probably a truncated/partial capture.

## What it would take to support WoR

A separate WoR parser mode in the engine:

1. Detect WoR packets by identifier `6101004c00` (and/or presence of a
   `LUA_CHANNELCHAT_ROOM_*` template key).
2. **Blocklist the template keys** (`LUA_*`, `ROOM_NAME_*`, `ROOM_DESC_*`, and
   any other `*_NN` string-table refs) from name extraction.
3. Extract the real names by scanning UTF-16 runs after the template key (skip
   templates), since offsets drift.
4. Map template key → direction (`ROOM_DESC_30` vs `ROOM_NAME_37` = killed vs
   slain — confirm which is which).
5. Identify the army/faction arg and the killer/victim order.

## Open questions (need a clean capture to answer)

- Which template key is "killed" vs "was slain by"? (direction mapping)
- Of the 3 name args: which is killer, which is victim, which is the army? Is the
  army one of those 3, or a separate field?
- Are there really only 3 args, or do family+character both appear?
- What is identifier `6901002e00` (event 4)?

## What we need to capture next

With logger **1.14.0** the `.pcap` now saves reliably (to `Documents/CoGM
Logger`). To finish this:

1. A **clean `.pcap` of a full WoR fight** (dozens of events).
2. **2-3 screenshots of the in-game feed** during that fight, so we can
   cross-reference packet → message and lock the direction + arg semantics.

## Evidence on hand

- `docs/samples/wor-raw-session-1781475471805.log` — the 5-event raw session
  (the only raw WoR data we have so far; mostly garbled but carries the hex).
- In-game feed screenshot + the "what we tracked" screenshot (in the chat
  history, 2026-06-14).
- Captured 2026-06-14 from a real WoR fight ("Arise"-side player, `Poxont` etc.).

See also `docs/kill-detection.md` for the node-war parser internals (offsets,
extract_string, the identifier/name-scan logic the WoR mode would branch from).
