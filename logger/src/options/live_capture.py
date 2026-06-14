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

identifier_regex = r"[56][0-9a-f]0100[0-9a-f]{4}"
name_regex = r"^[A-Z][a-zA-Z0-9_]{2,15}$"


def package_handler(package, output, ip_filter=True, record_pcap_path=None):
    global last_payload, _pcap_writer, _pcap_count

    if "IP" not in package:
        return

    package_src = package["IP"].src

    # checks if the package derives from bdo
    is_bdo_ip = (not ip_filter) or (
        len(
            (
                [
                    ip
                    for ip in ["20.76.13", "20.76.14", "13.64.17", "13.93.181"]
                    if ip in package_src
                ]
            )
        )
        > 0
    )

    # checkes if the packages comes from a tcp stream
    uses_tcp = "TCP" in package and hasattr(package["TCP"].payload, "load")
    if is_bdo_ip and uses_tcp:
        # Write the raw packet to the always-on pcap before parsing so we still
        # capture subtype packets that don't yield a 5-name match. Goes through
        # the single long-lived _pcap_writer (opened in start_sniff) instead of
        # a per-packet open+append, which lagged the sniff thread and dropped
        # packets at war rates.
        if _pcap_writer is not None:
            try:
                _pcap_writer.write(package)
                _pcap_count += 1
                if _pcap_count % 100 == 0:
                    _pcap_writer.flush()
            except Exception as exc:
                print(f"pcap write failed: {exc}", flush=True)

        # loads the payload as raw hex
        payload = bytes(package["TCP"].payload).hex()

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

            if len(payload) >= 600:
                possible_log = payload[0:600]
                i = 0
                names = []
                while i < 600:
                    name = extract_string(possible_log, i, 64)
                    if name == -1:
                        i += 1
                        continue
                    is_valid = re.match(name_regex, name)
                    if is_valid:
                        names.append(name + " " + str(i))
                        i += 64
                    else:
                        i += 1
                if len(names) == 5:
                    time = strftime("%I:%M:%S", localtime(int(package.time)))
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
    if file != None and not os.path.isfile(file):
        print("Invalid file", flush=True)
        return
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
    global _log_file, _pcap_writer, _pcap_count
    try:
        print("Reading Network...", flush=True)
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
