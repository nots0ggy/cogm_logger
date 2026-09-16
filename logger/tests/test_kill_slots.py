"""Session slot-lock: RAT 14.09 junk must not emit or retune a live war.

The 14.09 capture started with 5x0100/6x0100 near-misses (`ad`×5, `V9`/`A9`)
before the real 6601003e14 kills. Those used to overwrite _name_slots. These
tests pin the lock-once gate and check a real 5-name kill still emits.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

LOGGER_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, LOGGER_ROOT)

sys.modules.setdefault("scapy", MagicMock())
sys.modules.setdefault("scapy.all", MagicMock())

from src.options import live_capture as lc  # noqa: E402


SLOTS = [12, 142, 272, 404, 528]
RAT_FIRST = ["Sage_CD", "SoJEnjoyer", "Haram", "Vaphroy", "High_CD"]
# Real second kill: family==character on both sides, only 3 distinct names.
RAT_DUP = ["Howyyy", "Derakhil", "DIDDY", "Derakhil", "Howyyy"]
RAT_1409_RAW = "/Users/ramimamar/Downloads/14.09.2026.raw.log"


def _fields(names, slots=None):
    slots = slots or SLOTS
    return [f"{n} {o}" for n, o in zip(names, slots)]


class TestKillSlotLock(unittest.TestCase):
    def setUp(self):
        lc._reset_session_parse_state()

    def tearDown(self):
        lc._reset_session_parse_state()

    def test_junk_ad_does_not_lock_or_emit(self):
        names = _fields(["ad"] * 5, [106, 216, 326, 436, 546])
        self.assertIsNone(lc._fit_names_to_slots(names, "00" * 300, "65010000ff"))
        self.assertIsNone(lc._name_slots)

    def test_junk_v9_does_not_lock(self):
        names = _fields(["V9", "A9", "r9", "A9", "A9"], [100, 214, 328, 442, 556])
        self.assertIsNone(lc._fit_names_to_slots(names, "00" * 300, "5d01000000"))
        self.assertIsNone(lc._name_slots)

    def test_empty_suffix_opcode_rejected_even_with_real_looking_names(self):
        names = _fields(["Alpha", "Bravo", "Charlie", "Delta", "Echo"])
        self.assertIsNone(lc._fit_names_to_slots(names, "00" * 300, "6601000000"))
        self.assertIsNone(lc._name_slots)

    def test_clean_rat_first_kill_locks_slots(self):
        names = _fields(RAT_FIRST)
        out = lc._fit_names_to_slots(names, "00" * 300, "6601003e14")
        self.assertEqual(out, names)
        self.assertEqual(lc._name_slots, SLOTS)
        self.assertEqual(lc._locked_opcode, "6601003e14")

    def test_family_equals_character_still_locks(self):
        # Requiring 5 unique strings would drop this real 14.09 kill.
        names = _fields(RAT_DUP)
        out = lc._fit_names_to_slots(names, "00" * 300, "6601003e14")
        self.assertIsNotNone(out)
        self.assertEqual(lc._name_slots, SLOTS)

    def test_junk_cannot_overwrite_locked_slots(self):
        lc._fit_names_to_slots(_fields(RAT_FIRST), "00" * 300, "6601003e14")
        junk = _fields(["ad"] * 5, [106, 216, 326, 436, 546])
        self.assertIsNone(lc._fit_names_to_slots(junk, "00" * 300, "5401006a11"))
        self.assertEqual(lc._name_slots, SLOTS)

    def test_two_char_name_emits_after_lock(self):
        lc._fit_names_to_slots(_fields(RAT_FIRST), "00" * 300, "6601003e14")
        later = _fields(["Jo", "HitMeBaby", "Haram", "Horukk", "Jon1"])
        out = lc._fit_names_to_slots(later, "00" * 300, "6601003e14")
        self.assertIsNotNone(out)
        self.assertEqual(out[0], "Jo 12")

    def test_four_name_row_slots_onto_lock(self):
        lc._fit_names_to_slots(_fields(RAT_FIRST), "00" * 300, "6601003e14")
        partial = _fields(
            ["Sage_CD", "Haram", "Vaphroy", "High_CD"],
            [12, 272, 404, 528],
        )
        out = lc._fit_names_to_slots(partial, "00" * 300, "6601003e14")
        self.assertIsNotNone(out)
        self.assertEqual(len(out), 5)
        self.assertIn("Unknown 142", out)

    def test_reset_clears_lock(self):
        lc._fit_names_to_slots(_fields(RAT_FIRST), "00" * 300, "6601003e14")
        lc._reset_session_parse_state()
        self.assertIsNone(lc._name_slots)
        self.assertIsNone(lc._locked_opcode)

    def test_junk_then_real_locks_on_real(self):
        junk = _fields(["ad"] * 5, [106, 216, 326, 436, 546])
        self.assertIsNone(lc._fit_names_to_slots(junk, "00" * 300, "65010000ff"))
        out = lc._fit_names_to_slots(_fields(RAT_FIRST), "00" * 300, "6601003e14")
        self.assertIsNotNone(out)
        self.assertEqual(lc._name_slots, SLOTS)


class TestRat1409Replay(unittest.TestCase):
    """Replay the real RAT session CSV through the slot gate when the file is here."""

    def setUp(self):
        lc._reset_session_parse_state()

    def tearDown(self):
        lc._reset_session_parse_state()

    @unittest.skipUnless(os.path.isfile(RAT_1409_RAW), "RAT 14.09 raw log not on disk")
    def test_replay_drops_junk_keeps_real_opcode(self):
        kept = []
        with open(RAT_1409_RAW) as f:
            for line in f:
                parts = line.strip().split(",", 7)
                if len(parts) < 8:
                    continue
                opcode = parts[0]
                names = parts[2:7]
                out = lc._fit_names_to_slots(names, parts[7].strip()[:600], opcode)
                if out is not None:
                    kept.append(opcode)

        self.assertNotIn("65010000ff", kept)
        self.assertNotIn("5401006a11", kept)
        self.assertNotIn("5d01000000", kept)
        self.assertNotIn("5201000000", kept)
        self.assertNotIn("6601000000", kept)
        self.assertTrue(all(op == "6601003e14" for op in kept), set(kept))
        self.assertGreaterEqual(len(kept), 1236)
        self.assertEqual(lc._name_slots, SLOTS)


if __name__ == "__main__":
    unittest.main()
