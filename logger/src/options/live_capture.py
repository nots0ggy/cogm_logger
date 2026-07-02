import os
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


last_payload = ""
current_position = 0

# Open recovery log file for the live session. start_sniff opens it once and
# package_handler appends each parsed record so a crash leaves a readable
# file behind. None when not live-capturing (e.g. offline pcap analysis).
_log_file = None

# Persistent full-packet pcap writer for the always-on capture. start_sniff
# opens it once (append=False, sync=False); package_handler writes through it.
# A single long-lived writer avoids the per-packet open+append that lagged the
# sniff thread and dropped packets at war rates. None when not recording.
_pcap_writer = None
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
_DIAG_SAMPLE_CAP = 300
_DIAG_SUMMARY_EVERY = 500

identifier_regex = r"[56][0-9a-f]0100[0-9a-f]{4}"
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
            f"names_dist={{{dist}}}\n"
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


def package_handler(package, output, ip_filter=True, record_pcap_path=None):
    global last_payload, _pcap_writer, _pcap_count

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
        if _pcap_writer is not None:
            record_this = (
                (package_src in _locked_ips or package_dst in _locked_ips)
                if locked
                else re.search(identifier_regex, payload) is not None
            )
            if record_this:
                try:
                    _pcap_writer.write(package)
                    _pcap_count += 1
                    if _pcap_count % 100 == 0:
                        _pcap_writer.flush()
                except Exception as exc:
                    print(f"pcap write failed: {exc}", flush=True)

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
        ):
            return

        # iterate through the payload and try to find the identifier + player names + guild name + kill
        payload = last_payload + payload
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
                possible_log = payload[0:726]
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

        last_payload = payload[position:]


def open_pcap(file, output, ip_filter=True, record_pcap_path=None):
    global _locked_ips, _lock_counts
    if file != None and not os.path.isfile(file):
        print("Invalid file", flush=True)
        return
    # Fresh server lock per replay, same as start_sniff — a stale lock from a
    # previous file in the same process would silently zero the next one.
    _locked_ips = set()
    _lock_counts = {}
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


def start_sniff(output, all_interfaces=True, ip_filter=True, record_pcap_path=None):
    global _log_file, _pcap_writer, _pcap_count, _locked_ips, _lock_counts
    global _diag_file, _diag_counts, _diag_samples, _diag_candidates
    try:
        print("Reading Network...", flush=True)
        # Fresh server lock per session (see _locked_ips above).
        _locked_ips = set()
        _lock_counts = {}
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
            except Exception as pcap_err:
                _pcap_writer = None
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
        guidToNameDict = read_network_interfaces()
        intfList = get_if_list()
        namesAllowedList = [guidToNameDict.get(e) for e in intfList]
        namesAllowedList = list(filter(None, namesAllowedList))

        sniff(
            filter="tcp",
            prn=lambda x: package_handler(x, output, ip_filter, record_pcap_path),
            store=0,
            iface=namesAllowedList if len(
                namesAllowedList) > 0 and all_interfaces else None,
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
