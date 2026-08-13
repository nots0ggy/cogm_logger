import os
import queue
import threading
import re
import sys
from scapy.all import sniff, rdpcap, get_if_list
from time import localtime, strftime
from .capture_errors import emit_capture_error


def dec(bytes):
    message = str(bytes, "latin-1")
    message = message.replace("\x00", "")
    return message


def extract_string(hex, offset, length):
    # check whether the string begins with a 0x00, if so, return -1
    if hex[offset: offset + 2] == "00":
        return -1

    # check whether the characters are always spaced by 1 byte (0x00), if not, return -1
    test_offset = offset + 2
    actual_length = length
    while test_offset < offset + length - 2:
        byte = hex[test_offset: test_offset + 2]
        previous_byte = hex[test_offset - 2: test_offset]

        if previous_byte == "00":
            actual_length = test_offset - offset
            break
        if byte != "00":
            return -1
        test_offset += 4

    try:
        actual_length = min(len(hex) - offset, actual_length)
        if length < 0:
            raise ValueError("Package too short")

        return dec(bytes.fromhex(hex[offset: offset + actual_length]))
    except ValueError as e:
        # print(e, flush=True)
        return -1


# Per-source reassembly tails, keyed by the packet's source IP. This used to be
# one global buffer shared by every stream, which was fine when we captured a
# single interface: kill packets only ever came from one war server. Capturing
# on every interface (1.30) means segments from different sources can interleave
# into the same buffer and corrupt each other's reassembly. Kill packets come
# FROM the war server, so the source IP is the stream identity that matters.
# Bounded: a tail is trimmed on every parse pass, and the dict is cleared when
# it collects too many sources (pre-lock browsing noise).
_stream_tails = {}
# New-format (2026-08-13) per-source buffers; consumed as records emit.
_new_tails = {}
_STREAM_TAILS_MAX = 128
_TAIL_MAX_HEX = 40_000
current_position = 0

# Record-level dedup. The same kill segment can be delivered more than once:
# the same frame visible on two captured interfaces (VPN + physical NIC,
# bridged/virtual switches) or a plain TCP retransmission. The parser is
# stateless per delivery, so each copy used to emit an identical record and
# every duplicated kill counted double. A kill record's full payload hex is
# byte-identical across deliveries, so suppress repeats of the same payload
# seen within a short window. Two DISTINCT kills are never byte-identical
# (coords, counters and ids differ), so a 10s window cannot eat a real kill.
_recent_records = {}
_RECENT_MAX = 4096
_DEDUP_WINDOW_S = 10
_dups_suppressed = 0

# Open recovery log file for the live session. start_sniff opens it once and
# package_handler appends each parsed record so a crash leaves a readable
# file behind. None when not live-capturing (e.g. offline pcap analysis).
_log_file = None

# Persistent full-packet pcap writer for the always-on capture. start_sniff
# opens it once (append=False, sync=False); package_handler writes through it.
# A single long-lived writer avoids the per-packet open+append that lagged the
# sniff thread and dropped packets at war rates. None when not recording.
_pcap_writer = None
# Packets waiting to be written to the pcap, drained by a background thread.
#
# The write used to happen inline in package_handler, which IS scapy's prn and
# runs on the single capture thread. PcapWriter.write() calls raw(pkt) and
# rebuilds the whole dissected packet: measured at 42.8 us/packet against
# 1.5 us for the hex extraction that is the entire rest of the job, so the
# capture thread did 5.5x the work per war-server packet. That cost lands in
# both directions once the server lock is acquired, which is minutes into every
# war, and it is worst in the biggest fights — precisely when kills matter and
# precisely when officers compare their board against someone running ikusa.
#
# Bounded on purpose. If the disk cannot keep up the pcap loses packets, which
# is a research nicety; the capture thread must never block, because that loses
# KILLS. dropped_pcap_packets is reported once at the end rather than per
# packet: the old per-packet print on a full disk flooded stdout, which the UI
# reads line by line, and the backpressure stalled the sniffer it was meant to
# be warning about.
_pcap_queue = None
_pcap_thread = None
_pcap_dropped = 0
# Hard ceiling on the always-on pcap. It stays always-on because the raw bytes
# are what let us recalibrate an opcode after a BDO patch (that is how the
# 6b -> 640 kill-offset break was recovered), but a busy 2h war writes ~600
# bytes per packet continuously and can reach several GB. Past the cap we stop
# recording and say so once: the first slice of a war is enough to recalibrate,
# and filling a user's disk mid-war is a far worse outcome.
_PCAP_MAX_BYTES = 512 * 1024 * 1024
_pcap_bytes = 0
_pcap_capped = False
_pcap_count = 0

# Diagnostic sidecar for "0 logs" reports. package_handler tallies how many names
# each candidate kill-packet yielded and samples the off-by-one near-misses (4 or
# 6+ names). A healthy war has ZERO of those: every candidate is a clean 5-name
# kill or an obvious non-kill (0-3 names). So any near-miss here is the smoking
# gun for a silent kill drop (the kind that caused the 1.16 "0 logs" regression,
# where the widened window made every kill parse to 6 names). start_sniff opens
# the file and resets the counters; best-effort, a diag error never touches
# capture. None when not live-capturing.
_diag_file = None
_diag_counts = {}       # names_count -> candidates that yielded it
_diag_samples = 0       # near-miss sample lines written (capped)
_diag_candidates = 0    # total candidates seen this session

# Packets this attempt actually delivered. sniff() blocks for the whole session
# and the UI stops capture by KILLING the process, so a normal return means the
# attempt never bound to anything rather than that the user stopped. Counting
# packets is what lets the interface loop tell those apart instead of treating a
# silent no-op as a successful capture.
_packets_seen = 0
_DIAG_SAMPLE_CAP = 300
_DIAG_SUMMARY_EVERY = 500

identifier_regex = r"[56][0-9a-f]0100[0-9a-f]{4}"

# ── 2026-08-13 patch: the second kill format ─────────────────────────────────
# The August 13 game update replaced the 5-name fixed-offset kill packet with a
# marker-based record (docs/patch-2026-08-13-new-kill-format.md). Byte-identical
# in every observed kill, and absent from 178MB of pre-patch war traffic:
#
#   2e 03 01 00 03 01 02 00 00   record signature
#   a7 00 00 ae 1c               identity marker #1 (killer: char, family)
#   ... variable 200-400 bytes ...
#   a7 00 00 ae 1c               identity marker #2 (victim: char, family)
#
# The gap between the two identity blocks VARIES, so this cannot be expressed
# as registry offsets; the blocks are found by scanning for the marker. Names
# sit at fixed positions within each block: char at marker+5 bytes, family at
# marker+71 bytes.
#
# The emitted line keeps the exact 8-field shape of the old format (identifier,
# time, five "name offset" columns, hex) by padding column 5 with the literal
# guild "Unknown" (the record carries no guild name). Unlike the old packets,
# these records are a GLOBAL zone feed with no implicit "our side": the UI
# orients each record against the alliance roster and drops third-party kills
# (orient_observer_log in packet-registry.ts) before stats or upload see it.
# The registry entry for identifier 2e03010003 maps killer/victim to the
# family columns and points the kill flag at hex char 5 of the signature,
# which is a constant '1' (every record IS a kill; direction is block order,
# killer first, matching the on-screen feed "[A] slaughtered [B]").
NEW_SIG_HEX = "2e0301000301020000a70000ae1c"
NEW_MARK_HEX = "a70000ae1c"
# From signature start: enough to hold both identity blocks at the largest
# observed span (~700 bytes) with headroom.
NEW_WINDOW_HEX = 1600
# char at +5B, family at +71B from each marker start.
NEW_CHAR_OFF_HEX = len(NEW_MARK_HEX)      # 10 hex chars after the marker
NEW_FAM_OFF_HEX = 71 * 2 - 10             # relative to char position
# The gap between the two identity blocks varies per record, but the UI and
# CoGM decode names by ONE fixed offset per column across a whole war. So the
# emitted hex is NORMALIZED: signature+killer block, then the victim block
# spliced to a fixed position, then a literal "Unknown" guild field (UTF-16LE,
# zero-padded like a real name field), then real trailing wire bytes for
# dedup entropy. The raw wire bytes stay available in the session pcap and
# the server-side raw session. Resulting constant offsets:
#   killer char 28, killer family 160, victim char 250, victim family 382,
#   guild 480, kill flag at hex char 5 (inside the signature, constantly '1':
#   every record IS a kill; direction is block order, killer first).
NEW_BLOCK_HEX = 240                        # hex chars kept per identity block
NEW_UNKNOWN_FIELD_HEX = "55006e006b006e006f0077006e00".ljust(64, "0")
name_regex = r"^[A-Z][a-zA-Z0-9_]{2,15}$"

# War-server IPs learned this session. A source locks in after it produces
# _LOCK_THRESHOLD parsed kill records — one record could conceivably be a
# fluke payload from unrelated traffic, and a false lock would misdirect the
# pcap scope for the whole session. A mid-war channel swap just locks the new
# server the same way (kill packets always pass the identifier pre-check, so
# the swap is picked up even with the IP filter on). Replaces the old
# hardcoded BDO IP list, which went stale when BDO moved Azure ranges. Reset
# per session in start_sniff/open_pcap.
_locked_ips = set()
_lock_counts = {}
_LOCK_THRESHOLD = 3


def _diag_write_summary():
    # One-line snapshot of what the parser is seeing. Written periodically (so a
    # mid-war crash still leaves a trail) and once at session end. The names_dist
    # is the at-a-glance diagnosis: a healthy war reads kills(5-name)>0 with the
    # rest at 0-3 names; a regression shows up as a spike at 4 or 6+.
    if _diag_file is None:
        return
    try:
        dist = ", ".join(f"{n}:{_diag_counts[n]}" for n in sorted(_diag_counts))
        kills = _diag_counts.get(5, 0)
        stamp = strftime("%H:%M:%S", localtime())
        _diag_file.write(
            f"SUMMARY {stamp} candidates={_diag_candidates} kills(5-name)={kills} "
            f"names_dist={{{dist}}} dups_suppressed={_dups_suppressed}\n"
        )
        _diag_file.flush()
    except Exception:
        pass


def _diag_record(names_count, names, hexstr, package):
    # Tally one candidate and, for the off-by-one near-misses (4 or 6+ names),
    # write a sample with its names+offsets and full hex so the failure can be
    # diagnosed offline. Best-effort: a diag error must never break capture.
    global _diag_candidates, _diag_samples
    if _diag_file is None:
        return
    try:
        _diag_counts[names_count] = _diag_counts.get(names_count, 0) + 1
        _diag_candidates += 1
        if (names_count == 4 or names_count >= 6) and _diag_samples < _DIAG_SAMPLE_CAP:
            _diag_samples += 1
            stamp = strftime("%H:%M:%S", localtime(int(package.time)))
            _diag_file.write(
                f"NEAR-MISS {stamp} names={names_count} "
                f"[{' | '.join(names)}] hex={hexstr}\n"
            )
            _diag_file.flush()
        if _diag_candidates % _DIAG_SUMMARY_EVERY == 0:
            _diag_write_summary()
    except Exception:
        pass


def _scan_new_format(payload, package, package_src):
    """Detect and emit 2026-08-13-format kill records.

    Keeps its own per-source reassembly buffer (_new_tails) and CONSUMES each
    emitted record from it, so a record is emitted exactly once no matter how
    the stream is segmented. Independent of the old detector's buffer: the two
    formats never share bytes, and neither scan disturbs the other.
    """
    global _dups_suppressed
    buf = _new_tails.get(package_src, "") + payload
    pos = 0
    retain_from = None
    while True:
        idx = buf.find(NEW_SIG_HEX, pos)
        if idx < 0:
            break

        # First identity marker is the tail of the signature itself.
        m1 = idx + len(NEW_SIG_HEX) - len(NEW_MARK_HEX)

        # Second identity block: the next marker within the record window.
        # The gap between blocks varies (208 and 400 bytes observed), which
        # is why this is a scan and not a registry offset.
        m2 = buf.find(NEW_MARK_HEX, m1 + len(NEW_MARK_HEX), idx + NEW_WINDOW_HEX)
        if m2 < 0:
            if len(buf) >= idx + NEW_WINDOW_HEX:
                # A full window with a single identity block is a different
                # message type (28 of 30 marker records in the reference
                # capture are singles); skip past it.
                pos = idx + len(NEW_SIG_HEX)
                continue
            # The rest of the record is still in flight.
            retain_from = idx
            break

        # Wait until the victim's family field has fully arrived.
        rec_end = m2 + NEW_CHAR_OFF_HEX + NEW_FAM_OFF_HEX + 64
        if len(buf) < rec_end:
            retain_from = idx
            break

        k_char = extract_string(buf, m1 + NEW_CHAR_OFF_HEX, 64)
        k_fam = extract_string(buf, m1 + NEW_CHAR_OFF_HEX + NEW_FAM_OFF_HEX, 64)
        v_char = extract_string(buf, m2 + NEW_CHAR_OFF_HEX, 64)
        v_fam = extract_string(buf, m2 + NEW_CHAR_OFF_HEX + NEW_FAM_OFF_HEX, 64)
        names_ok = all(
            isinstance(n, str) and re.match(name_regex, n)
            for n in (k_char, k_fam, v_char, v_fam)
        )
        if not names_ok:
            pos = idx + len(NEW_SIG_HEX)
            continue

        # Normalized emit hex (see NEW_BLOCK_HEX above). Block slices are
        # zero-padded to constant width so column offsets never move; the
        # completeness check above only guarantees bytes through rec_end.
        emit_hex = (
            buf[idx : idx + NEW_BLOCK_HEX].ljust(NEW_BLOCK_HEX, "0")
            + buf[m2 : m2 + NEW_BLOCK_HEX].ljust(NEW_BLOCK_HEX, "0")
            + NEW_UNKNOWN_FIELD_HEX
            + buf[m2 + NEW_BLOCK_HEX : m2 + NEW_BLOCK_HEX + 64]
        )

        # Duplicate delivery (second interface or TCP retransmission) of a
        # record this consuming buffer already emitted from another copy of
        # the stream. Same guard as the old detector.
        now_ts = float(package.time)
        seen_ts = _recent_records.get(emit_hex)
        _recent_records[emit_hex] = now_ts
        if len(_recent_records) > _RECENT_MAX:
            cutoff = now_ts - _DEDUP_WINDOW_S
            for k in list(_recent_records):
                if _recent_records[k] < cutoff:
                    del _recent_records[k]
        if seen_ts is not None and now_ts - seen_ts <= _DEDUP_WINDOW_S:
            _dups_suppressed += 1
            pos = rec_end
            continue

        # Same lock semantics as the old detector: repeated kill records from
        # one source is what identifies the war server.
        if package_src not in _locked_ips:
            _lock_counts[package_src] = _lock_counts.get(package_src, 0) + 1
            if _lock_counts[package_src] >= _LOCK_THRESHOLD:
                _locked_ips.add(package_src)
                print(f"Locked onto war server {package_src}", flush=True)

        time = strftime("%H:%M:%S", localtime(int(package.time)))
        # The exact 8-field line shape of the old format, with the constant
        # normalized offsets. Column 5 is the guild: this record carries no
        # guild name, so it points at the embedded "Unknown" field. Block
        # order is killer first, matching the on-screen feed; if ground truth
        # flips that, the fix is a UI hotfix (registry name_order + the
        # orient_observer_log field constants), and raw sessions re-decode
        # server-side for wars already uploaded.
        line = (
            emit_hex[0:10]
            + ","
            + time
            + ","
            + f"{k_char} 28"
            + ","
            + f"{k_fam} 160"
            + ","
            + f"{v_char} 250"
            + ","
            + f"{v_fam} 382"
            + ","
            + f"Unknown 480"
            + ","
            + emit_hex
        )
        print(line, flush=True)
        if _log_file is not None:
            try:
                _log_file.write(line + "\n")
                _log_file.flush()
            except Exception:
                pass
        pos = rec_end

    if retain_from is not None:
        buf = buf[retain_from:]
        # A partial record that outgrew any plausible split is stuck garbage
        # (same guard as the old detector's tail cap).
        if len(buf) > _TAIL_MAX_HEX:
            buf = buf[-3200:]
    else:
        # No signature in flight; keep just enough for one split across the
        # segment boundary. Everything before it can never match again.
        keep = len(NEW_SIG_HEX) - 2
        buf = buf[max(0, len(buf) - keep) :] if pos == 0 else buf[max(pos, len(buf) - keep) :]
    if buf:
        _new_tails[package_src] = buf
    else:
        _new_tails.pop(package_src, None)
    if len(_new_tails) > _STREAM_TAILS_MAX:
        for src in list(_new_tails):
            if src not in _locked_ips:
                del _new_tails[src]


def package_handler(package, output, ip_filter=True, record_pcap_path=None):
    global _pcap_writer, _pcap_count, _packets_seen, _pcap_dropped, _dups_suppressed

    _packets_seen += 1

    if "IP" not in package:
        return

    package_src = package["IP"].src
    package_dst = package["IP"].dst

    # checkes if the packages comes from a tcp stream
    uses_tcp = "TCP" in package and hasattr(package["TCP"].payload, "load")
    if uses_tcp:
        # Dynamic server lock replaces the old hardcoded BDO IP list (which
        # went stale when BDO moved Azure ranges and would have silently
        # zeroed the capture — see _locked_ips above).
        locked = len(_locked_ips) > 0

        # loads the payload as raw hex
        payload = bytes(package["TCP"].payload).hex()

        # Write to the always-on pcap so subtype packets that don't yield a
        # 5-name match are still captured for protocol research. Once locked,
        # record the war server's traffic in BOTH directions (this runs before
        # the parse gate below on purpose — outbound client->server packets
        # never carry the identifier). Before the lock, keep only packets that
        # look like kill candidates so the file doesn't fill with the user's
        # unrelated traffic. Goes through the single long-lived _pcap_writer
        # (opened in start_sniff) instead of a per-packet open+append, which
        # lagged the sniff thread and dropped packets at war rates.
        if _pcap_writer is not None and _pcap_queue is not None:
            record_this = (
                (package_src in _locked_ips or package_dst in _locked_ips)
                if locked
                else (
                    re.search(identifier_regex, payload) is not None
                    or NEW_MARK_HEX in payload
                )
            )
            if record_this:
                # Never blocks. A full queue means the writer is behind, and
                # dropping a pcap packet is strictly better than stalling the
                # capture thread and dropping a kill.
                try:
                    _pcap_queue.put_nowait(package)
                except Exception:
                    _pcap_dropped += 1

        # IP-filter parse gate: skip payloads from hosts that are not a locked
        # war server unless they carry the kill-packet identifier themselves.
        # Kill packets always match, so the pre-lock discovery and a mid-war
        # channel swap both keep working; everything else is dropped before it
        # can touch the shared last_payload parse buffer. Off by default —
        # with the filter off, behaviour is unchanged (parse everything).
        if (
            ip_filter
            and package_src not in _locked_ips
            and re.search(identifier_regex, payload) is None
            and NEW_MARK_HEX not in payload
        ):
            return

        # iterate through the payload and try to find the identifier + player names + guild name + kill
        if len(_stream_tails) > _STREAM_TAILS_MAX:
            # Pre-lock noise from many sources; keep only locked war servers.
            for src in list(_stream_tails):
                if src not in _locked_ips:
                    del _stream_tails[src]
        # New-format kills (2026-08-13 patch). Runs alongside the old
        # detector rather than replacing it, so a rollback or a straggler
        # server on the old format keeps recording. Keeps its OWN per-source
        # reassembly buffer and consumes emitted records from it, so it must
        # see the raw segment payload, not the old detector's tail-prefixed
        # buffer (that tail would be re-appended on every packet).
        _scan_new_format(payload, package, package_src)

        payload = _stream_tails.get(package_src, "") + payload

        position = 0
        while len(payload[position:]) >= 600:
            payload = payload[position:]
            position = 0
            match_location = 0
            matches = list(re.finditer(identifier_regex, payload))

            if len(matches) == 0:
                return  # no match found, return - could cause issue if the identifier is split between two packages
            elif len(matches) == 1:
                match_location = matches[0].start()
            else:
                while len(matches) > 1:
                    if matches[0].start() + 600 < matches[1].start():
                        match_location = matches[0].start()
                        break
                    elif len(matches) > 2:
                        matches.pop(0)
                    else:
                        match_location = matches[1].start()
                        break

            payload = payload[match_location:]

            # Emit the full 363-byte kill packet (726 hex) so the tail floats
            # (bytes 350-358 = hex 700-716 = the kill's world X/Y/Z) ride along
            # for the kill-location map when they're in the buffer.
            #
            # Name detection must stay on exactly the first 600 hex. The five
            # names live there, and scanning into the 600-726 tail lets a string
            # in that region (or the coordinate bytes) register as a spurious
            # sixth name, which makes len(names) != 5 and silently drops every
            # such kill (the "0 logs" regression). name_window keeps detection
            # byte-identical to the proven pre-coords behaviour; possible_log
            # only widens what we ship downstream.
            if len(payload) >= 600:
                # 1600 hex (800 bytes), widened from 726 on 2026-08-07. The
                # patch packet 6a0100ce0b stopped carrying the FIELDED character
                # in the five known name slots (it sends some other char of the
                # family, which misclassed 25 players in one night). If the real
                # fielded char exists anywhere in this packet, it is past the
                # old 363-byte cutoff, so ship a deeper tail and let the server
                # analyze it. Name detection stays on the first 600 hex; the
                # tail may include bytes of the NEXT packet in the buffer, which
                # downstream decoding already tolerates (it did at 726 too).
                possible_log = payload[0:1600]
                name_window = payload[0:600]
                i = 0
                names = []
                while i < 600:
                    name = extract_string(name_window, i, 64)
                    if name == -1:
                        i += 1
                        continue
                    is_valid = re.match(name_regex, name)
                    if is_valid:
                        names.append(name + " " + str(i))
                        i += 64
                    else:
                        i += 1
                # Diagnostic tally: record what this candidate parsed to so a
                # "0 logs" session leaves evidence of how the kills degraded
                # (best-effort, no-op when the diag file isn't open).
                _diag_record(len(names), names, possible_log, package)
                if len(names) == 5:
                    # Duplicate delivery of the same kill segment (second
                    # interface or TCP retransmission): identical payload seen
                    # moments ago. Skip the emit AND the server-lock counting so
                    # a duplicated fluke can't help a false lock either.
                    now_ts = float(package.time)
                    seen_ts = _recent_records.get(possible_log)
                    _recent_records[possible_log] = now_ts
                    if len(_recent_records) > _RECENT_MAX:
                        cutoff = now_ts - _DEDUP_WINDOW_S
                        for k in list(_recent_records):
                            if _recent_records[k] < cutoff:
                                del _recent_records[k]
                    if seen_ts is not None and now_ts - seen_ts <= _DEDUP_WINDOW_S:
                        _dups_suppressed += 1
                        position = 600
                        continue
                    # A kill record: count towards locking onto its server
                    # (threshold guards against a fluke false-positive payload
                    # hijacking the session — see _locked_ips).
                    if package_src not in _locked_ips:
                        _lock_counts[package_src] = _lock_counts.get(package_src, 0) + 1
                        if _lock_counts[package_src] >= _LOCK_THRESHOLD:
                            _locked_ips.add(package_src)
                            print(f"Locked onto war server {package_src}", flush=True)
                    time = strftime("%H:%M:%S", localtime(int(package.time)))
                    line = (
                        payload[0:10]
                        + ","
                        + time
                        + ","
                        + ",".join(names)
                        + ","
                        + possible_log
                    )
                    print(line, flush=True)
                    # Durability: write each captured record straight to disk
                    # so a PC crash mid-war can't wipe the session (the UI
                    # otherwise holds logs only in memory until Save). Flush
                    # per line; kills are infrequent so the cost is nil, and a
                    # crash loses at most the in-flight line. Best-effort: a
                    # disk error must never break capture.
                    if _log_file is not None:
                        try:
                            _log_file.write(line + "\n")
                            _log_file.flush()
                        except Exception:
                            pass
                    position = 600
                else:
                    position = 1

            else:
                break

        tail = payload[position:]
        # A tail that outgrew any plausible split packet is stuck garbage;
        # keep the recent end where a genuinely split kill could still live.
        if len(tail) > _TAIL_MAX_HEX:
            tail = tail[-3200:]  # two full 800-byte emit windows in hex
        _stream_tails[package_src] = tail


def open_pcap(file, output, ip_filter=True, record_pcap_path=None):
    global _locked_ips, _lock_counts, _stream_tails, _new_tails, _recent_records, _dups_suppressed
    if file != None and not os.path.isfile(file):
        print("Invalid file", flush=True)
        return
    # Fresh server lock per replay, same as start_sniff — a stale lock from a
    # previous file in the same process would silently zero the next one.
    _locked_ips = set()
    _lock_counts = {}
    _stream_tails = {}
    _new_tails = {}
    _recent_records = {}
    _dups_suppressed = 0
    print("Reading " + file, flush=True)
    if os.name == "nt":
        print("Loading file into ram. This may take a while.", flush=True)
        cap = rdpcap(file)
        index = 0
        for package in cap:
            package_handler(package, output, ip_filter, record_pcap_path)
            if index % 10000 == 0:
                print(f"{index}/{len(cap)} packages analyzed.", flush=True)
            index += 1
    else:
        sniff(
            offline=file,
            filter="tcp",
            prn=lambda x: package_handler(x, output, ip_filter, record_pcap_path),
            store=0,
        )

    print(f"Logs saved under: {output}\nYou can close this window now.", flush=True)


def read_network_interfaces():
    if sys.platform == "win32":
        # Use importlib so PyInstaller's static modulegraph doesn't try to
        # actually import scapy.arch.windows during build. On a Windows CI
        # runner without Npcap, that import partially fails and modulegraph
        # marks every file referencing it as "invalid", silently dropping
        # it from the bundle. collect_submodules('scapy') in logger.spec
        # still bundles the module for runtime use.
        import importlib
        mod = importlib.import_module("scapy.arch.windows")
        winList = mod.get_windows_if_list()
        return {e["guid"]: e["name"] for e in winList}

    else:
        # Use Linux-specific function
        return {iface: iface for iface in get_if_list()}


def _pcap_worker():
    """Drain the pcap queue onto disk. Runs off the capture thread on purpose."""
    global _pcap_count, _pcap_bytes, _pcap_capped
    while True:
        package = _pcap_queue.get()
        if package is None:  # shutdown sentinel
            break
        if _pcap_capped:
            continue
        try:
            _pcap_bytes += len(package)
            if _pcap_bytes > _PCAP_MAX_BYTES:
                _pcap_capped = True
                print(
                    f"pcap: reached {_PCAP_MAX_BYTES // (1024 * 1024)}MB, stopping raw capture "
                    "for this session. Kill logging continues normally.",
                    flush=True,
                )
                continue
            _pcap_writer.write(package)
            _pcap_count += 1
            if _pcap_count % 100 == 0:
                _pcap_writer.flush()
        except Exception:
            # Swallow per-packet failures. A disk that has filled would
            # otherwise print once per packet, and the UI reads stdout line by
            # line, so the flood stalls the very capture this is meant to be
            # protecting. The total is reported once at shutdown.
            pass


def start_sniff(output, all_interfaces=True, ip_filter=True, record_pcap_path=None):
    global _log_file, _pcap_writer, _pcap_count, _locked_ips, _lock_counts
    global _pcap_queue, _pcap_thread, _pcap_dropped, _pcap_bytes, _pcap_capped
    global _diag_file, _diag_counts, _diag_samples, _diag_candidates
    # _packets_seen is incremented by package_handler on the module. Without it
    # declared here the assignment below binds a local, the "did this interface
    # deliver anything" check always reads 0, and a capture that worked is
    # reported as a dead driver.
    global _packets_seen, _stream_tails, _new_tails, _recent_records, _dups_suppressed
    try:
        print("Reading Network...", flush=True)
        # Fresh server lock + reassembly/dedup state per session (see the
        # module-level docs on _stream_tails and _recent_records).
        _locked_ips = set()
        _lock_counts = {}
        _stream_tails = {}
        _new_tails = {}
        _recent_records = {}
        _dups_suppressed = 0
        if record_pcap_path is not None:
            # Absolute path so the UI can show the user exactly where the
            # full-packet capture lands (for sharing it in for research).
            print(f"Saving pcap to {os.path.abspath(record_pcap_path)}", flush=True)
            # One long-lived writer for the whole session. append=False starts a
            # fresh file; sync=False avoids an fsync per packet. The old
            # per-packet open+append lagged the sniff thread and dropped packets
            # at war rates. Lazy import (PyInstaller modulegraph). makedirs
            # first: captures/ may not exist yet.
            try:
                from scapy.utils import PcapWriter
                pcap_dir = os.path.dirname(record_pcap_path)
                if pcap_dir:
                    os.makedirs(pcap_dir, exist_ok=True)
                _pcap_writer = PcapWriter(record_pcap_path, append=False, sync=False)
                _pcap_count = 0
                _pcap_dropped = 0
                _pcap_bytes = 0
                _pcap_capped = False
                # ~2s of headroom at a heavy war rate. Big enough to absorb a
                # disk hiccup, small enough to bound memory.
                _pcap_queue = queue.Queue(maxsize=2000)
                _pcap_thread = threading.Thread(target=_pcap_worker, daemon=True)
                _pcap_thread.start()
            except Exception as pcap_err:
                _pcap_writer = None
                _pcap_queue = None
                print(f"pcap capture unavailable: {pcap_err}", flush=True)
        # Open the durable recovery file before sniffing. makedirs because the
        # default output lives under logger/.tmp which may not exist yet.
        try:
            directory = os.path.dirname(output)
            if directory:
                os.makedirs(directory, exist_ok=True)
            _log_file = open(output, "a", encoding="utf-8", errors="replace")
        except Exception as file_err:
            _log_file = None
            print(f"Recovery file unavailable: {file_err}", flush=True)
        # Diagnostic sidecar next to the recovery log (session-<ts>.diag.log).
        # Best-effort and independent of the recovery file: it's the trail that
        # turns a silent "0 logs" report into evidence. Reset the per-session
        # counters here so a re-armed capture starts clean.
        _diag_counts = {}
        _diag_samples = 0
        _diag_candidates = 0
        try:
            diag_path = (output[:-4] if output.lower().endswith(".log") else output) + ".diag.log"
            diag_dir = os.path.dirname(diag_path)
            if diag_dir:
                os.makedirs(diag_dir, exist_ok=True)
            _diag_file = open(diag_path, "a", encoding="utf-8", errors="replace")
            _diag_file.write(f"=== capture session started {strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            _diag_file.flush()
        except Exception as diag_err:
            _diag_file = None
            print(f"Diagnostic log unavailable: {diag_err}", flush=True)
        # Interface selection, most-capable first, each falling back to the next.
        #
        # We used to do only the middle one: map every scapy interface id
        # through read_network_interfaces() and pass the names that survived.
        # Two ways that captures nothing. A GUID missing from the map is
        # silently dropped from the list, and if scapy then cannot open what is
        # left, sniff() raises and the whole session ends with "Error while
        # reading network" and zero kills. Upstream fixed both in
        # sch-28/ikusa_logger (0812dd3 "capture on all interfaces (Windows)" and
        # 0bb3a48 "compatibility interface fallback"), which landed in the
        # analyze.py we had already replaced, so we never picked them up. A user
        # on such a machine gets a silent partial or empty log and concludes the
        # logger is inaccurate.
        #
        # Order matters: raw get_if_list() first because it is what scapy itself
        # expects and needs no lossy name mapping; the GUID map second for the
        # Windows builds where the raw ids do not open; iface=None last, which
        # lets scapy pick the default interface and is better than not capturing.
        prn = lambda x: package_handler(x, output, ip_filter, record_pcap_path)

        attempts = []
        if all_interfaces:
            raw = get_if_list()
            if raw:
                attempts.append(("all interfaces", raw))
            guidToNameDict = read_network_interfaces()
            namesAllowedList = list(filter(None, (guidToNameDict.get(e) for e in raw)))
            if namesAllowedList and namesAllowedList != raw:
                attempts.append(("named interfaces", namesAllowedList))
        attempts.append(("default interface", None))

        last_error = None
        captured = False
        for label, iface in attempts:
            try:
                print(f"Capturing on {label}...", flush=True)
                _packets_seen = 0
                sniff(filter="tcp", prn=prn, store=0, iface=iface)
                # sniff returned without raising. Because the UI kills this
                # process to stop a capture, that is not a user stop: either the
                # interface delivered nothing and scapy gave up, or it never
                # bound. Only treat it as real if packets actually arrived,
                # otherwise fall through and try the next option. Without this
                # a silent no-op looks identical to a working capture and the
                # war logs zero kills.
                if _packets_seen > 0:
                    captured = True
                    break
                print(f"{label} delivered no packets, trying the next option.", flush=True)
            except Exception as sniff_err:
                last_error = sniff_err
                print(f"{label} capture failed: {sniff_err}", flush=True)

        if not captured:
            if last_error is not None:
                raise last_error
            raise RuntimeError(
                "No network interface delivered any packets. Check that the capture "
                "driver (Npcap on Windows) is installed and that the logger is running "
                "as administrator."
            )
    except Exception as e:
        # Keep the legacy line for older status parsing, and add the
        # classified CAPTURE_ERROR line the UI uses for actionable guidance.
        print("Error while reading network.", flush=True)
        emit_capture_error(e)
    finally:
        if _log_file is not None:
            try:
                _log_file.flush()
                _log_file.close()
            except Exception:
                pass
            _log_file = None
        if _pcap_queue is not None and _pcap_thread is not None:
            try:
                _pcap_queue.put_nowait(None)  # sentinel
                _pcap_thread.join(timeout=5)
            except Exception:
                pass
            if _pcap_dropped:
                print(
                    f"pcap: dropped {_pcap_dropped} packets (disk could not keep up). "
                    "Kill capture was unaffected.",
                    flush=True,
                )
            if _dups_suppressed:
                print(
                    f"Suppressed {_dups_suppressed} duplicate kill deliveries "
                    "(retransmissions or the same packet on multiple interfaces).",
                    flush=True,
                )
            _pcap_queue = None
            _pcap_thread = None
        if _pcap_writer is not None:
            try:
                _pcap_writer.flush()
                _pcap_writer.close()
            except Exception:
                pass
            _pcap_writer = None
        if _diag_file is not None:
            try:
                _diag_write_summary()
                _diag_file.write("=== capture session ended ===\n")
                _diag_file.flush()
                _diag_file.close()
            except Exception:
                pass
            _diag_file = None
