"""Pre-war capture self-test.

Answers the one question that matters before a war starts: if kills happen
right now, will this machine capture them? The worst data-loss mode left is
RECORDING for two hours while the capture path was broken the whole time
(wrong interface, wedged Npcap, elevation) and finding out after the war.

The test is end-to-end and uses no kill packets, so it works at a node
before combat: find the running game's actual TCP connection, then confirm
packets from that server are visible to the same interface-attempt ladder
the real capture uses. Seeing the game's own traffic through the capture
driver is exactly the path a kill packet will take.

Output is line-oriented for the UI:
  SELFTEST OK iface=<label> server=<ip> packets=<n>
  SELFTEST GAME_NOT_RUNNING | NO_CONNECTION | NO_TRAFFIC | UNSUPPORTED_OS | ERROR msg=...
"""

import subprocess
import sys

from scapy.all import sniff, get_if_list

_GAME_EXES = ("BlackDesert64.exe", "BlackDesert32.exe")
_SNIFF_SECONDS = 8


def _game_pids():
    pids = []
    for exe in _GAME_EXES:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
        except Exception:
            continue
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == exe.lower():
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    pass
    return pids


def _remote_ips(pids):
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return []
    ips = []
    want = {str(p) for p in pids}
    for line in out.splitlines():
        cols = line.split()
        # TCP local remote state pid. The state column is LOCALIZED (German
        # Windows says HERGESTELLT, not ESTABLISHED), so never filter on it:
        # PID ownership plus a real remote address is language-independent,
        # and listening/bound rows are excluded by their 0.0.0.0 / [::] remote.
        if len(cols) == 5 and cols[0].upper() == "TCP" and cols[4] in want:
            remote = cols[2].rsplit(":", 1)[0]
            if (
                remote
                and not remote.startswith("127.")
                and not remote.startswith("[::")
                and remote != "0.0.0.0"
                and remote != "*"
                and remote not in ips
            ):
                ips.append(remote)
    return ips


def run_self_test():
    if sys.platform != "win32":
        print("SELFTEST UNSUPPORTED_OS", flush=True)
        return
    try:
        pids = _game_pids()
        if not pids:
            print("SELFTEST GAME_NOT_RUNNING", flush=True)
            return
        ips = _remote_ips(pids)
        if not ips:
            # Game process exists but no established TCP: launcher/loading.
            print("SELFTEST NO_CONNECTION", flush=True)
            return

        bpf = "tcp and (" + " or ".join(f"host {ip}" for ip in ips) + ")"

        # Same attempt ladder as the real capture (see start_sniff): raw scapy
        # ids first, GUID-mapped names second, default interface last.
        from .live_capture import read_network_interfaces
        attempts = []
        raw = get_if_list()
        if raw:
            attempts.append(("all interfaces", raw))
        try:
            guid_map = read_network_interfaces()
            named = list(filter(None, (guid_map.get(e) for e in raw)))
            if named and named != raw:
                attempts.append(("named interfaces", named))
        except Exception:
            pass
        attempts.append(("default interface", None))

        for label, iface in attempts:
            seen = {"n": 0, "src": ""}

            def count(pkt):
                seen["n"] += 1
                if not seen["src"] and "IP" in pkt:
                    seen["src"] = pkt["IP"].src

            try:
                print(f"SELFTEST TRYING {label}", flush=True)
                sniff(filter=bpf, prn=count, store=0, timeout=_SNIFF_SECONDS, iface=iface)
            except Exception as err:
                print(f"SELFTEST ATTEMPT_FAILED iface={label} msg={err}", flush=True)
                continue
            if seen["n"] > 0:
                print(
                    f"SELFTEST OK iface={label} server={seen['src'] or ips[0]} packets={seen['n']}",
                    flush=True,
                )
                return

        print("SELFTEST NO_TRAFFIC", flush=True)
    except Exception as err:
        print(f"SELFTEST ERROR msg={err}", flush=True)
