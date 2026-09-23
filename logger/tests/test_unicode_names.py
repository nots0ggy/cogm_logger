"""Thai names in kill records (SEA node war, 2026-09-23, opcode 670100e111).

extract_string only takes UTF-16 units with a 00 high byte, so a family like
"Jสmbสng" (J, U+0E2A, m, b, U+0E2A, n, g) never decoded: that player's kills
were dropped and his board read 3/0 instead of 16/5. These tests pin the
strict Unicode path: real Thai names decode, including ones that start with a
tone mark, binary garbage stays rejected, and ASCII decoding is unchanged.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock

LOGGER_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, LOGGER_ROOT)

sys.modules.setdefault("scapy", MagicMock())
sys.modules.setdefault("scapy.all", MagicMock())

from src.options import live_capture as lc  # noqa: E402


def _field(name):
    """A 32-byte name field as the game sends it: UTF-16LE, zero padded."""
    return name.encode("utf-16le").hex().ljust(64, "0")


# Byte-exact fields from the 2026-09-23 capture.
JAMBANG = "4a002a0e6d0062002a0e6e0067000000" + "0" * 32
MARK_FIRST = _field("่Ryuu")  # "่Ryuu", starts with U+0E48
FULL_16 = _field("ทูน่าป่าเดียวดาย")  # 16 units, no terminator
# "Unknown 402" field from the session log: binary that decodes to CJK noise.
GARBAGE = "00fc1a00b20ba2c7506088c5014c9047cd95015a001c8700cad02b3f00000000"

# A real died-to record from the capture (first 600 bytes of the packet):
# Zhanilia / MaidDragon / ่่CrazyShark / Jสmbสng / ่่CrazyShark.
REAL_RECORD = (
    "670100e1115a00680061006e0069006c0069006100000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000006000000004d0061006900640044007200610067006f006e0000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000480e480e430072006100"
    "7a00790053006800610072006b0000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000001ff00004a002a0e6d0062002a0e6e00670000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000480e480e4300720061007a00790053006800610072006b0000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000ffabb6be0000000045eb"
    "a2c7670794c5e475a8470000010000540082dfe40d4700430000fc1a000e9371c714a33dc55275904799140775006c7e"
    "00bb31b3be000000001ccf6f3f0e9371c714a33dc5527590470000000000000000000000000000fcffffa200005d1700"
    "000000006c7e000100000000000e9371c714a33dc552759047000000000000000090230b00d2e16c240000fcffff0000"
    "00000000000000000000ffff00000e9371c714a33dc55275904787a9b10200000000bb31b3be000000001ccf6f3f00fc"
    "ffff00effd72c75e2238c5f25a92470000000000000000cdcccc3d2a000000000000000000000000000000000008b72d"
    "06060000001f2001000100002d0000580fe78000fcffff34"
)


class TestUnicodeField(unittest.TestCase):
    def test_jambang_decodes(self):
        self.assertEqual(lc.extract_string(JAMBANG, 0, 64), -1)  # the old bug
        self.assertEqual(lc._extract_name(JAMBANG, 0, 64), "Jสmbสng")
        self.assertTrue(lc._is_valid_name("Jสmbสng"))

    def test_combining_mark_first_decodes(self):
        self.assertEqual(lc._extract_name(MARK_FIRST, 0, 64), "่Ryuu")
        self.assertEqual(
            lc._extract_name(_field("่่Hawari"), 0, 64), "่่Hawari"
        )

    def test_full_sixteen_unit_name_decodes(self):
        self.assertEqual(lc._extract_name(FULL_16, 0, 64), "ทูน่าป่าเดียวดาย")

    def test_sixteen_units_ending_in_thai_keeps_first_letter(self):
        # extract_string never checks unit 16's high byte, so it returns
        # latin-1 with a stray 0x0e here instead of -1. The scan used to skip
        # the real start and accept "ETSGETITBABYYY่" four hex chars later.
        field = _field("LETSGETITBABYYY่") + "0000"
        self.assertIn("\x0e", lc.extract_string(field, 0, 64))
        self.assertEqual(lc._extract_name(field, 0, 64), "LETSGETITBABYYY่")
        window = "00" * 8 + field + "00" * 32
        self.assertEqual(lc._extract_name(window, 16, 64), "LETSGETITBABYYY่")

    def test_garbage_field_rejected(self):
        self.assertEqual(lc.extract_string(GARBAGE, 0, 64), -1)
        self.assertEqual(lc._extract_name(GARBAGE, 0, 64), -1)
        # Every scan offset into the garbage, not just the aligned one.
        window = GARBAGE + "0" * 64
        for i in range(len(GARBAGE)):
            self.assertEqual(lc.extract_unicode_string(window, i, 64), -1, i)

    def test_misaligned_reads_of_thai_field_rejected(self):
        window = "00" * 8 + JAMBANG + "00" * 32
        for shift in (1, 2, 3, 5, 6, 7):
            self.assertEqual(lc.extract_unicode_string(window, 16 + shift, 64), -1, shift)

    def test_bytes_after_terminator_rejected(self):
        # Name, terminator, then non-zero bytes inside the field: not a name.
        dirty = JAMBANG[:32] + "0000" + "4100" + "0" * 24
        self.assertEqual(lc.extract_unicode_string(dirty, 0, 64), -1)

    def test_truncated_field_needs_visible_terminator(self):
        # Cut before the terminator: nothing proves this is a whole name.
        self.assertEqual(lc.extract_unicode_string(JAMBANG[:20], 0, 64), -1)
        self.assertEqual(lc.extract_unicode_string(JAMBANG[:28], 0, 64), -1)
        # 6c0100aa19 (2026-09-06) puts its fifth name at hex 538, so the 600-hex
        # scan window cuts the field at 62 chars. "่Oby" must still decode
        # there, or the scan finds "Oby" at 542 and locks the wrong slot.
        window = ("00" * 269 + _field("\u0e48Oby"))[:600]
        self.assertEqual(len(window) - 538, 62)
        self.assertEqual(lc._extract_name(window, 538, 64), "\u0e48Oby")
        # A cut that leaves a non-zero half unit is rejected.
        self.assertEqual(lc.extract_unicode_string(JAMBANG[:30] + "4", 0, 64), -1)

    def test_other_scripts_and_marks_only_rejected(self):
        self.assertEqual(lc._extract_name(_field("中文名"), 0, 64), -1)  # CJK
        self.assertEqual(lc._extract_name(_field("가나"), 0, 64), -1)  # Hangul
        self.assertEqual(lc._extract_name(_field(""), 0, 64), -1)  # private use
        self.assertEqual(lc._extract_name(_field("่่"), 0, 64), -1)  # marks only
        self.assertEqual(lc._extract_name(_field("๑Ab"), 0, 64), -1)  # leading digit

    def test_ascii_fields_unchanged(self):
        for name in ("JoJonoobb32", "MuhamadSodnamNgo", "Sage_CD", "Jo"):
            f = _field(name)
            self.assertEqual(lc._extract_name(f, 0, 64), lc.extract_string(f, 0, 64))
            self.assertEqual(lc._extract_name(f, 0, 64), name)
        # An all-ASCII field never goes through the Unicode path.
        self.assertEqual(lc.extract_unicode_string(_field("Alpha"), 0, 64), -1)
        # And ASCII validity is still exactly name_regex.
        for bad in ("1abc", "_abc", "a", "A" * 17):
            self.assertFalse(lc._is_valid_name(bad))


class _Payload:
    def __init__(self, raw):
        self.load = raw

    def __bytes__(self):
        return self.load


class _FakePacket:
    def __init__(self, raw, t=1790168929.0):
        self._layers = {
            "IP": SimpleNamespace(src="211.188.27.133", dst="192.168.0.7"),
            "TCP": SimpleNamespace(payload=_Payload(raw)),
        }
        self.time = t

    def __contains__(self, key):
        return key in self._layers

    def __getitem__(self, key):
        return self._layers[key]


class TestThaiRecordEndToEnd(unittest.TestCase):
    def setUp(self):
        lc._reset_session_parse_state()

    def tearDown(self):
        lc._reset_session_parse_state()

    def test_real_record_emits_all_five_names(self):
        # cp1252 stdout, like a Windows pipe: printing Thai there raises, which
        # would end the capture. The line must come out as UTF-8 instead.
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="cp1252")
        with redirect_stdout(out):
            lc.package_handler(_FakePacket(bytes.fromhex(REAL_RECORD)), "unused", False, None)
            out.flush()
        lines = [l for l in raw.getvalue().decode("utf-8").splitlines() if l.startswith("670100e111")]
        self.assertEqual(len(lines), 1)
        fields = lines[0].split(",")
        self.assertEqual(
            fields[2:7],
            [
                "Zhanilia 10",
                "MaidDragon 144",
                "่่CrazyShark 268",
                "Jสmbสng 402",
                "่่CrazyShark 526",
            ],
        )
        self.assertEqual(lc._name_slots, [10, 144, 268, 402, 526])

    def test_emit_line_ascii_path_unchanged(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            lc._emit_line("670100e111,21:00:00,A 10")
        self.assertEqual(buf.getvalue(), "670100e111,21:00:00,A 10\n")


if __name__ == "__main__":
    unittest.main()
