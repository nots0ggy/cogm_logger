import sys

# On non-Windows builds scapy's __init__ tries to load scapy.arch.windows.
# Stub it before anything imports scapy. Must run at the very top of the
# entry point — PyInstaller's static analyzer chokes if this lives in
# analyze.py at module top-level (silently drops the whole module).
if sys.platform != "win32":
    import types
    sys.modules.setdefault("scapy.arch.windows", types.ModuleType("scapy.arch.windows"))

from src import config
from src.options import status_check, open, sniff, record, update_config, full_capture, live_capture as analyze

import time
from time import localtime, strftime
from configparser import ConfigParser
from argparse import ArgumentParser, BooleanOptionalAction
from datetime import date
import os.path
import os
from sys import exit

parser = ArgumentParser()
parser.add_argument("-f", "--file", dest="filename",
                    help="Instead of sniffing for bdo packages, it will use the given *.pcap file", metavar="FILE")
parser.add_argument("-o", "--output",
                    default=f"logger/.tmp/{date.today()}.log",
                    help="custom output file")
parser.add_argument("-r", "--record",
                    help="Record all of BDO's traffic and save it to a pcap file", action= BooleanOptionalAction)
parser.add_argument("-s", "--status",
                    help="Check the status of all requirements", action= BooleanOptionalAction)
parser.add_argument("-u", "--update",
                    help="Update the config", action= BooleanOptionalAction)
parser.add_argument("-a", "--analyze",
                    help="Analyze network", action= BooleanOptionalAction)
parser.add_argument("-i", "--allInterfaces",
                    help="Sniff all interfaces", action= BooleanOptionalAction)
parser.add_argument("-p", "--ipFilter",
                    help="Enable Ip Filter to improve performance", action= BooleanOptionalAction)
parser.add_argument("-F", "--full",
                    help="Capture the full raw payload of all BDO traffic (pcap + jsonl) for protocol research", action= BooleanOptionalAction)


args = parser.parse_args()

config.init("config.ini")

# When -r is combined with -a, save the full raw capture with a readable,
# sortable name in the same folder as the recovery session (Documents/CoGM
# Logger), so it's easy to find and send in for protocol research. Bare -r
# (no -a) keeps its legacy behaviour via record.record().
if args.record and args.analyze:
    from time import localtime, strftime

    # Put the .pcap next to the session file (-o), which the UI sets to
    # Documents/CoGM Logger. start_sniff creates that dir (guarded), so the old
    # unguarded makedirs("captures") under a non-writable install dir is gone.
    # dirname falls back to "." for a bare -o.
    capture_dir = os.path.dirname(args.output) or "."
    pcap_path = os.path.join(
        capture_dir, "capture-" + strftime("%Y-%m-%d_%H-%M-%S", localtime()) + ".pcap"
    )
else:
    pcap_path = None

if args.status:
    status_check.check_health()
    exit()
elif args.full:
    full_capture.start_full_capture(args.output, args.allInterfaces)
    exit()
elif args.update:
    update_config.update_config()
elif args.analyze and args.filename != None:
    # Reading a pcap to write a pcap is nonsensical; force off.
    analyze.open_pcap(args.filename, args.output, args.ipFilter, None)
    exit()
elif args.analyze:
    analyze.start_sniff(args.output, args.allInterfaces, args.ipFilter, pcap_path)
    exit()
elif args.record:
    record.record(args.output)
    exit()
elif args.filename != None:
    open.open_pcap(args.filename, args.output)
    exit()
else:
    sniff.start_sniff(args.output)
    exit()
    
    
