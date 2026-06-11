"""
Full raw payload capture for protocol research.

Unlike the live combat parser (which keeps only a 300-byte window around the
combat-log identifier and extracts four fields), this mode records the entire
TCP payload of every packet from BDO's server IPs, losslessly, so the byte
layout of currently-unparsed packet types (gear, class, damage, position,
objectives) can be reverse-engineered offline.

It writes two artifacts side by side:
  - <output>.pcap   full packets, opened in Wireshark or scapy
  - <output>.jsonl  one line per packet: time, addresses, seq, payload hex,
                    for grep/diff/pandas analysis without scapy

This path never touches the live combat logging. It is a developer tool;
the captured stream is the same unencrypted BDO server traffic the combat
logger already reads, just kept in full.
"""

import json
import os

from scapy.all import sniff, PcapWriter

from .. import config


def _bpf_filter():
    """Kernel-level BPF from the configured server IP prefixes.

    Filtering in the kernel (not in Python after dissection) is what keeps a
    busy war night from overrunning the single capture thread and dropping
    packets. Prefixes are 3 octets (a /24) in every shipped config; 2-octet
    prefixes fall back to /16. Anything malformed degrades to plain "tcp".
    """
    nets = []
    for prefix in config.config.ips:
        prefix = (prefix or "").strip()
        octets = prefix.split(".")
        if len(octets) == 3 and all(o.isdigit() for o in octets):
            nets.append(f"net {prefix}.0/24")
        elif len(octets) == 2 and all(o.isdigit() for o in octets):
            nets.append(f"net {prefix}.0.0/16")
    if not nets:
        return "tcp"
    return "tcp and (" + " or ".join(nets) + ")"


def start_full_capture(output, all_interfaces=True):
    base = os.path.splitext(output)[0]
    pcap_path = base + ".pcap"
    jsonl_path = base + ".jsonl"

    directory = os.path.dirname(pcap_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    bpf = _bpf_filter()
    print("Reading Network...", flush=True)
    print(f"Full capture -> {pcap_path}", flush=True)
    print(f"Payload index -> {jsonl_path}", flush=True)

    # Open both writers once. Per-packet open/append (the old record mode's
    # bug) is far too slow at war packet rates.
    pcap_writer = PcapWriter(pcap_path, append=False, sync=False)
    jsonl_file = open(jsonl_path, "w", encoding="utf-8")

    count = {"n": 0}

    def handle(package):
        if "IP" not in package or "TCP" not in package:
            return
        tcp = package["TCP"]
        if not hasattr(tcp.payload, "load"):
            return
        payload = bytes(tcp.payload)
        if not payload:
            return

        pcap_writer.write(package)
        jsonl_file.write(
            json.dumps(
                {
                    "time": float(package.time),
                    "src": package["IP"].src,
                    "dst": package["IP"].dst,
                    "sport": int(tcp.sport),
                    "dport": int(tcp.dport),
                    "seq": int(tcp.seq),
                    "len": len(payload),
                    "hex": payload.hex(),
                }
            )
            + "\n"
        )

        count["n"] += 1
        # Periodic flush so a crash or force-quit mid-war still leaves a
        # readable capture, and progress is visible in the console.
        if count["n"] % 100 == 0:
            pcap_writer.flush()
            jsonl_file.flush()
            print(f"Captured {count['n']} packets", flush=True)

    try:
        from scapy.all import get_if_list

        iface = None
        if all_interfaces:
            iface_list = get_if_list()
            if iface_list:
                iface = iface_list
        sniff(
            filter=bpf,
            prn=handle,
            store=0,
            iface=iface,
        )
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print("Error while reading network.", flush=True)
        print(e, flush=True)
    finally:
        pcap_writer.flush()
        pcap_writer.close()
        jsonl_file.flush()
        jsonl_file.close()
        print(f"Full capture stopped. {count['n']} packets saved.", flush=True)
