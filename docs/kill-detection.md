# How kill/death detection works (and why the direction is noisy)

This is a full dissection of sch-28's capture and parsing pipeline, the part
that decides who killed whom. It exists because a real war (Ulukita, 12.06.2026)
produced K/D that was badly off versus the in-game guild board, and we needed to
know exactly where the error comes from. The short version: the engagements are
captured correctly, but the kill-versus-death direction is decided by a fragile
heuristic that lands on a near-miss byte, so roughly a third of events flip.

None of this logic is the CoGM fork's. It is byte-for-byte sch-28's upstream
(`github.com/sch-28/ikusa_logger`): the capture engine, the regexes, the
`config.ini` offsets, and `find_kill_offset` are all identical between the two
repos. The fork only added upload, crash recovery, the full-packet toggle, the
rebrand, and a Python 3.12 compile fix.

## The pipeline end to end

```
BDO TCP packet
  -> live_capture.py: find a kill-event packet, extract 5 names + a 600-hex window
  -> stdout line:  <id10>,<time>,<name0 off0>,..,<name4 off4>,<600-hex>
  -> logger.svelte: parse to LogType { identifier, time, names:[{name,offset}x5], hex }
  -> calculate_config(): group the 5 name positions into columns
  -> name-order: pick which column is Killer / Victim / Guild  (player_one/two/guild_index)
  -> find_kill_offset(): GUESS which hex byte is the kill flag
  -> get_logs_string(): emit "[t] Killer has killed Victim from Guild"  or  "... died to ..."
```

The two pieces that determine accuracy are the **name-order** (which column is the
killer) and the **kill flag** (was it a kill or a death). The name-order is a
global, per-war setting and is usually right or fixable. The kill flag is decided
per save by `find_kill_offset`, and that is the weak link.

## Stage 1 - packet capture (`logger/src/options/live_capture.py`)

- Sniff TCP from the BDO server IPs `20.76.13`, `20.76.14`, `13.64.17`, `13.93.181`.
- Take the TCP payload as a hex string, concatenated with any leftover from the
  previous packet (`last_payload`) so a record split across packets still parses.
- Find the kill-event marker with `identifier_regex = [56][0-9a-f]0100[0-9a-f]{4}`.
  That is a 10-hex-char tag: a `5` or `6`, any hex digit, the constant `0100`, then
  4 hex digits. `config.ini`'s `identifier = 630100af12` is one concrete instance.
  The constant `0100` in the middle is what actually marks a kill-feed packet.
- From the match, take a 600-hex-char window (`possible_log = payload[0:600]`,
  300 bytes). `log_length = 600`.

## Stage 2 - name extraction (5 names)

Scan the 600-char window. At each position try `extract_string(window, i, 64)`:
a name field is 64 hex chars (32 bytes, `name_length = 64`), UTF-16-ish with each
character followed by a `00` byte. `extract_string` returns -1 if the field does
not start with a printable byte or is not `00`-spaced. Each candidate is validated
with `name_regex = ^[A-Z][a-zA-Z0-9_]{2,15}$`. Valid names are recorded as
`"<name> <offset>"` and the scan jumps forward 64. **Only when exactly 5 names are
found is the record emitted.** So every kill packet carries 5 names at fixed
positions (family and character names of the two combatants plus the guild).

## Stage 3 - the emitted line

```
payload[0:10] + "," + time + "," + ",".join(names) + "," + possible_log
```

i.e. `630100af12,08:01:03,Rekless 402,Aglaea 526,Family 12,ReklessKnight 90,Permansor 240,<600 hex>`
(offsets illustrative). The UI parses this into a `LogType`.

## Stage 4 - name-order columns (`calculate_config`)

The 5 names sit at consistent positions in every packet. `calculate_config`
collects, for each of the 5 positions, the set of byte offsets seen across all
logs and counts their frequency, producing `possible_name_offsets[i]` sorted most
common first. `name_indicies[i]` (default 0) picks the most frequent offset for
column `i`. So the 5 columns are the 5 packet name positions.

`player_one_index` / `player_two_index` / `guild_index` (default 0 / 1 / 2) say
which column is the Killer, the Victim, and the Guild. That mapping is the
"name order" the UI lets you set, and it now persists in `config.name_order`.

If the name-order is wrong it is a **uniform** error: every line is mislabelled
the same way, so the board would look mirrored or shifted, not noisy. That is how
we know the 12.06 problem is NOT the name-order.

## Stage 5 - the kill flag (`find_kill_offset`) - the weak link

```js
function find_kill_offset(logs) {
  const all_indicies = [];
  for (const log of logs) {
    let indicies = find_all_indicies(log.hex, '01');        // every '01' substring, ANY index
    indicies = indicies.filter((index) =>
      log.names.every((n) => index > n.offset + 64 || index < n.offset)); // outside name fields
    all_indicies.push(...indicies);
  }
  const counts = new Map();
  for (const log of logs)
    for (const index of all_indicies)
      if (log.hex.slice(index, index + 2) === '00')          // count where it is '00'
        counts.set(index, (counts.get(index) || 0) + 1);
  return [...counts.entries()].sort((a,b)=>b[1]-a[1]).map(a => a[0] + 1); // most-'00' first, +1
}
```

The intent: the kill flag is one byte that reads `01` on a kill and `00` on a
death. Collect byte positions that are ever `01` (outside names), then pick the
one that is `00` most often. `kill_index = 0` takes the top candidate.
`get_logs_string` then reads `hex[offset] === '1'` to decide "has killed" vs
"died to". The `+1` targets the second nibble of the flag byte (`0`**`1`** vs
`0`**`0`**).

### Why it is unreliable

1. **It ranks by "most `00`", which is the wrong objective.** The true kill flag
   in a balanced war is about 50% `01` and 50% `00`, so it has relatively few
   `00`s. A byte that is mostly zero with the occasional `01` (a rare-event flag,
   a counter, padding) racks up far more `00`s and out-ranks the real flag. The
   chosen byte ends up only loosely correlated with direction.

2. **Mis-aligned matches pollute the candidates.** `find_all_indicies` uses
   `indexOf`, so `'01'` matches at any string index, including odd (mid-byte)
   positions. A `...X0`/`1Y...` boundary yields a spurious candidate that is not a
   real byte at all.

3. **No ground truth.** The heuristic cannot check itself against the actual
   board, so it cannot tell a 70%-correct byte from a 100%-correct one.

4. **The calibrated offset is thrown away.** `config.ini` carries
   `kill = 141` (and `player_one = 402`, `player_two = 526`, `guild = 12`), the
   values sch-28 calibrated for a known BDO build. `onMount` seeds
   `possible_kill_offsets = [config.kill]`, but the first `logs_changed` pass
   overwrites it with `find_kill_offset(logs)`. So during a live recording the
   calibration is discarded in favour of the guess. (The legacy offline
   `parser.py` does the opposite: it reads `payload[kill_offset]` directly, i.e.
   the calibrated byte, which is why offline parsing was the accurate path.)

### Evidence from the 12.06.2026 Ulukita war

1382 events. Compared to the in-game board:

- 36 of 49 players had the **exact** correct total events (kills+deaths). So the
  engagements were captured fine.
- Every player's kill-share was dragged toward 50%:
  Axelton 85% real -> 60% logged, PandaPanduh 85% -> 50%, LlamaSpeed 72% -> 50%,
  PockyChen 0% -> 77%, Validate 8% -> 77%.
- 2 players (Tails, IKrazyl) were missing entirely - a separate capture-gap issue,
  matching the "kept not getting logs" report.

"Everyone regresses to a coin flip" is the exact signature of a direction bit that
is about 70% correct: right engagements, ~30% of directions random.

## How to fix it

The fix needs raw packet bytes plus a known board. Two sources of raw bytes:
the `logger/.tmp/session-*.log` file (the 8-field lines that include the 600-hex
window, before Save deletes it) or a `.pcap` from the full-packet toggle.

**Recalibration procedure (the real fix):**

1. Get one war's raw records (hex) and that war's official per-player K/D.
2. Fix the name-order for that war (Killer/Victim/Guild columns).
3. For each candidate byte offset `o` in `[0, 600)`, excluding the name fields:
   - For every record, direction = `hex[o] === '1'`.
   - Resolve killer/victim with the known name-order and tally per-player K/D.
   - Score `o` by agreement with the official board.
4. The offset with the highest agreement is the true kill flag. It is probably
   `141` if the calibration still holds, or a shifted value if BDO patched the
   packet.
5. Lock it in: set `config.kill`, and stop letting `find_kill_offset` overwrite it
   during live capture (prefer the calibrated value, fall back to the heuristic
   only when there is no calibration).

**Cheaper interim test:** force the live path to use `config.kill` (141) instead
of the heuristic and re-check one war against the board. If 141 is still correct,
that alone fixes the direction. If not, run the full recalibration in step 3.

Until then: engagements and totals are trustworthy; per-player kill-versus-death
direction is about 70% accurate and biased toward 50/50.
