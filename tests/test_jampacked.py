# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""Format equivalence with v0.6, ring mode, snapshots and SampleLog."""

import random
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adafruit_jampacked.bank import HEADER_SIZE, SNAPSHOT_HEADER, Bank
from adafruit_jampacked.codec import PythonCodec
from adafruit_jampacked.samplelog import SAMPLES_PER_PAGE, SampleLog

DATA = Path(__file__).resolve().parent / "data"


def golden():
    for line in (DATA / "golden_v06.txt").read_text().splitlines():
        if line.startswith("#"):
            continue
        page, mode, payload = line.split()
        yield (
            bytes.fromhex("" if page == "-" else page),
            int(mode),
            bytes.fromhex("" if payload == "-" else payload),
        )


def temperatures():
    return [int(v) for v in (DATA / "ms8607_temp_c100.txt").read_text().split()]


class GoldenTests(unittest.TestCase):
    def test_encode_matches_v06_byte_for_byte(self):
        codec = PythonCodec()
        out = bytearray(128)
        cases = list(golden())
        self.assertGreater(len(cases), 60)
        for page, mode, payload in cases:
            self.assertEqual(codec.encode(page), (mode, payload))
            self.assertEqual(codec.decode_into(mode, payload, out, len(page)), len(page))
            self.assertEqual(bytes(out[: len(page)]), page)

    def test_all_modes_covered(self):
        modes = {mode for _, mode, _ in golden()}
        self.assertEqual(modes, {0, 1, 2})
        self.assertTrue(any(p[:2] == b"\x00\x01" for _, m, p in golden() if m == 2))


class RingTests(unittest.TestCase):
    def test_ring_keeps_newest_in_order(self):
        rng = random.Random(3)
        pages = [bytes(rng.randrange(256) for _ in range(128)) for _ in range(20)]
        bank = Bank(600, ring=True)
        for page in pages:
            self.assertTrue(bank.append(page))
            self.assertLessEqual(bank.used, bank.capacity)
        out = bytearray(128)
        kept = [bytes(out[:n]) for n in bank.pages(out)]
        self.assertEqual(kept, pages[-len(kept) :])
        self.assertEqual(bank.dropped_bytes, 128 * (20 - len(kept)))
        self.assertEqual(bank.logical_bytes, 128 * len(kept))

    def test_ring_mixed_sizes(self):
        rng = random.Random(4)
        bank = Bank(300, ring=True)
        history = []
        for _ in range(200):
            size = rng.randrange(1, 129)
            page = bytes(rng.randrange(4) for _ in range(size))
            history.append(page)
            self.assertTrue(bank.append(page))
        out = bytearray(128)
        kept = [bytes(out[:n]) for n in bank.pages(out)]
        self.assertEqual(kept, history[-len(kept) :])
        self.assertEqual(bank.dropped_bytes + bank.logical_bytes, sum(map(len, history)))

    def test_ring_needs_room_for_full_page(self):
        with self.assertRaises(ValueError):
            Bank(HEADER_SIZE + 127, ring=True)

    def test_without_ring_full_bank_refuses(self):
        bank = Bank(HEADER_SIZE + 128, packed=False)
        self.assertTrue(bank.append(bytes(128)))
        self.assertFalse(bank.append(b"x"))
        self.assertEqual(bank.count, 1)


class SnapshotTests(unittest.TestCase):
    def filled(self):
        bank = Bank(2048)
        for i in range(10):
            bank.append(struct.pack("<64H", *range(i, i + 64)))
        return bank

    def test_round_trip(self):
        bank = self.filled()
        mem = bytearray(4096)
        self.assertEqual(bank.save(mem, 7), SNAPSHOT_HEADER + bank.used)
        other = Bank(2048)
        self.assertTrue(other.load(mem, 7))
        out1, out2 = bytearray(128), bytearray(128)
        self.assertEqual(
            [bytes(out1[:n]) for n in bank.pages(out1)],
            [bytes(out2[:n]) for n in other.pages(out2)],
        )
        self.assertEqual((other.count, other.used, other.logical_bytes), (10, bank.used, 1280))

    def test_rejects_blank_and_corrupt(self):
        bank = self.filled()
        mem = bytearray(4096)
        other = Bank(2048)
        self.assertFalse(other.load(mem))
        bank.save(mem)
        for flip in (5, SNAPSHOT_HEADER + 3, SNAPSHOT_HEADER + bank.used - 1):
            bad = bytearray(mem)
            bad[flip] ^= 0x10
            self.assertFalse(other.load(bad))
            self.assertEqual((other.count, other.used), (0, 0))

    def test_does_not_fit(self):
        with self.assertRaises(ValueError):
            self.filled().save(bytearray(100))

    def test_too_big_for_capacity(self):
        mem = bytearray(4096)
        self.filled().save(mem)
        self.assertFalse(Bank(64).load(mem))


class SampleLogTests(unittest.TestCase):
    def test_real_temperature_packs_like_the_analysis(self):
        temps = temperatures()
        log = SampleLog(capacity=8192, scale=100, signed=True)
        self.assertEqual(log.extend(t / 100 for t in temps), len(temps))
        self.assertEqual(len(log), len(temps))
        self.assertEqual([round(v * 100) for v in log], temps)
        # 1370 readings: 21 packed pages, 26 waiting in the open page.
        self.assertEqual(log.bank.count, len(temps) // SAMPLES_PER_PAGE)
        self.assertGreater(log.ratio, 4.0)

    def test_int_scale_returns_ints(self):
        log = SampleLog()
        log.extend(range(200))
        self.assertEqual(list(log), list(range(200)))
        self.assertTrue(all(isinstance(v, int) for v in log))

    def test_offset_and_signed_ranges(self):
        log = SampleLog(signed=True)
        log.extend((-32768, 0, 32767))
        self.assertEqual(list(log), [-32768, 0, 32767])
        for bad in (-32769, 32768):
            with self.assertRaises(ValueError):
                log.append(bad)
        pressure = SampleLog(offset=-65536)
        pressure.extend((100330, 101000))
        self.assertEqual(list(pressure), [100330, 101000])
        with self.assertRaises(ValueError):
            SampleLog().append(-1)
        with self.assertRaises(ValueError):
            SampleLog(scale=0)

    def test_full_log_refuses_and_keeps_data(self):
        rng = random.Random(5)
        values = [rng.randrange(65536) for _ in range(1000)]
        log = SampleLog(capacity=300)
        stored = log.extend(values)
        self.assertLess(stored, 1000)
        self.assertFalse(log.append(1))
        self.assertEqual(list(log), values[:stored])

    def test_ring_counts_dropped(self):
        rng = random.Random(6)
        values = [rng.randrange(65536) for _ in range(1000)]
        log = SampleLog(capacity=600, ring=True)
        self.assertEqual(log.extend(values), 1000)
        self.assertEqual(len(log) + log.dropped, 1000)
        self.assertEqual(list(log), values[log.dropped :])

    def test_flush_and_snapshot_keep_open_page(self):
        temps = temperatures()[:150]
        log = SampleLog(capacity=4096, offset=32768)
        log.extend(temps)
        mem = bytearray(4096)
        log.save(mem)
        again = SampleLog(capacity=4096, offset=32768)
        self.assertTrue(again.load(mem))
        self.assertEqual(list(again), temps)
        again.extend(temps)
        self.assertEqual(list(again), temps + temps)
        self.assertTrue(again.flush())
        self.assertEqual(list(again), temps + temps)
        self.assertFalse(SampleLog().load(bytearray(64)))


if __name__ == "__main__":
    unittest.main()
