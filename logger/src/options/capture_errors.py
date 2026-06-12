"""
Classify packet-capture startup failures into actionable codes.

When sniffing fails, scapy raises an exception whose text identifies the
cause (Npcap missing, driver wedged after a crash, no admin rights, no
interface). The UI parses the CAPTURE_ERROR line emitted from this so it
can show the right fix and recovery button instead of a generic "something
went wrong". Codes:

  NPCAP_MISSING        - the capture driver isn't installed
  DRIVER_NOT_RESPONDING- installed but won't open the adapter (the classic
                         wedged-after-a-PC-crash state; fix = restart the
                         Npcap service or reboot)
  ACCESS_DENIED        - needs to run elevated
  NO_INTERFACE         - no usable network interface found
  UNKNOWN              - anything else; raw message carried through
"""

CAPTURE_ERROR_PREFIX = "CAPTURE_ERROR|"


def classify_capture_error(exc):
    text = str(exc).lower()

    if any(s in text for s in ("npcap", "winpcap", "wpcap", "libpcap", "pcap provider", "no libpcap")):
        # "Npcap is not installed" vs an installed-but-broken driver. The
        # adapter-open failures below are the wedged-driver case.
        if any(s in text for s in ("not installed", "could not find", "no such file", "cannot load")):
            return "NPCAP_MISSING"
        return "DRIVER_NOT_RESPONDING"

    if any(
        s in text
        for s in (
            "unable to open the adapter",
            "error opening adapter",
            "failed to set hardware filter",
            "the system cannot find the device",
            "failed to set the packet capture",
            "winerror 31",
            "device is not functioning",
        )
    ):
        return "DRIVER_NOT_RESPONDING"

    if any(
        s in text
        for s in (
            "access is denied",
            "permission denied",
            "operation not permitted",
            "winerror 5",
            "requires elevation",
            "administrator",
        )
    ):
        return "ACCESS_DENIED"

    if any(s in text for s in ("no such device", "no interface", "not a valid interface", "no devices")):
        return "NO_INTERFACE"

    return "UNKNOWN"


def emit_capture_error(exc):
    """Print the single structured line the UI parses, plus the raw text."""
    code = classify_capture_error(exc)
    # One machine-parseable line, then the raw exception for support logs.
    print(f"{CAPTURE_ERROR_PREFIX}{code}|{exc}", flush=True)
    return code
