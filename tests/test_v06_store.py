# SPDX-FileCopyrightText: Copyright (c) 2026 Adafruit Industries
# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""Ported from the jam packer v0.6 host tests."""

import array
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adafruit_jampacked.bank import HEADER_SIZE, Bank
from adafruit_jampacked.codec import PAGE_SIZE, RAW


class StoreTests(unittest.TestCase):
    def test_exact_accounting_and_roundtrip(self):
        pages = [b"j" * 128, bytes(range(128)), b"jam", b"\xff\x7f" * 64]
        for packed in (False, True):
            bank = Bank(8192, packed)
            backing_id = id(bank._data)
            out = bytearray(128)
            payload_total = 0
            for index, page in enumerate(pages):
                expected_mode, payload = bank.codec.encode(page) if packed else (RAW, page)
                self.assertTrue(bank.append(page))
                payload_total += len(payload)
                self.assertEqual(bank.read_into(index, out), len(page))
                self.assertEqual(out[: len(page)], page)
                self.assertEqual(bank.count, index + 1)
                self.assertEqual(bank.logical_bytes, sum(map(len, pages[: index + 1])))
                self.assertEqual(bank.used, payload_total + HEADER_SIZE * bank.count)
                self.assertEqual(bank.payload_bytes, payload_total)
                self.assertEqual(bank.header_bytes, HEADER_SIZE * bank.count)
            self.assertEqual(id(bank._data), backing_id)
            self.assertEqual(len(bank._data), bank.capacity)
            self.assertEqual(bank.scratch_bytes, PAGE_SIZE)
            for index in (3, 0, 2, 1):
                n = bank.read_into(index, out)
                self.assertEqual(out[:n], pages[index])

    def test_capacity_failure_is_atomic(self):
        for packed in (True, False):
            bank = Bank(272, packed)
            page = bytes(range(127))  # Deliberately not a 16-bit sample page.
            while bank.append(page):
                pass
            before = (bytes(bank._data), bank.used, bank.count, bank.logical_bytes)
            self.assertFalse(bank.append(page))
            self.assertEqual((bytes(bank._data), bank.used, bank.count, bank.logical_bytes), before)
            out = bytearray(128)
            for index in range(bank.count):
                n = bank.read_into(index, out)
                self.assertEqual(out[:n], page)
            for invalid in (
                memoryview(array.array("b", [-1] * 128)),
                memoryview(b"abcdef")[::2],
                memoryview(b"abcd").cast("B", shape=(2, 2)),
            ):
                with self.assertRaises(TypeError):
                    bank.append(invalid)
                self.assertEqual(
                    (bytes(bank._data), bank.used, bank.count, bank.logical_bytes), before
                )

    def test_exact_fit_and_first_failure(self):
        raw = Bank(136, False)
        self.assertTrue(raw.append(bytes(128)))
        self.assertEqual(raw.used, raw.capacity)
        self.assertFalse(raw.append(b"x"))
        too_small = Bank(9, False)
        before = bytes(too_small._data)
        self.assertFalse(too_small.append(b"xx"))
        self.assertEqual(bytes(too_small._data), before)
        self.assertEqual((too_small.used, too_small.count, too_small.logical_bytes), (0, 0, 0))

    def test_random_negative_control_has_identical_overhead(self):
        rng = random.Random(51)
        raw = Bank(8192, False)
        packed = Bank(8192, True)
        for _ in range(50):
            page = bytes(rng.randrange(256) for _ in range(128))
            self.assertEqual(packed.codec.encode(page)[0], RAW)
            self.assertTrue(raw.append(page))
            self.assertTrue(packed.append(page))
        self.assertEqual(
            (raw.used, raw.logical_bytes, raw.count),
            (packed.used, packed.logical_bytes, packed.count),
        )
        self.assertEqual(raw.header_bytes, packed.header_bytes)

    def test_every_single_byte_corruption_is_detected(self):
        for page in (b"a" * 128, bytes(range(127)), b"\xe8\x03" * 64):
            bank = Bank()
            bank.append(page)
            for offset in range(bank.used):
                bank._data[offset] ^= 1
                try:
                    with self.subTest(page_len=len(page), offset=offset):
                        with self.assertRaises(ValueError):
                            bank.read_into(0, bytearray(128))
                finally:
                    bank._data[offset] ^= 1
            out = bytearray(128)
            self.assertEqual(bank.read_into(0, out), len(page))
            self.assertEqual(out[: len(page)], page)

    def test_truncated_records_and_bounds(self):
        bank = Bank()
        bank.append(b"x" * 128)
        complete = bank.used
        for used in range(complete):
            bank.used = used
            with self.assertRaises(ValueError):
                bank.read_into(0, bytearray(128))
        bank.used = complete
        for index in (-1, 1, 1.0):
            with self.assertRaises(IndexError):
                bank.read_into(index, bytearray(128))
        with self.assertRaises(ValueError):
            bank.read_into(0, bytearray(127))
        for page in (b"", bytes(129)):
            with self.assertRaises(ValueError):
                bank.append(page)
        with self.assertRaises(ValueError):
            Bank(8)

    def test_corrupt_preceding_record_cannot_redirect_later_read(self):
        bank = Bank()
        bank.append(b"a" * 128)
        rng = random.Random(1)
        middle = bytes(rng.randrange(256) for _ in range(24))
        bank.append(middle)
        bank.append(b"c" * 128)
        out = bytearray(128)
        self.assertEqual(bank.read_into(1, out), 24)
        self.assertEqual(out[:24], middle)
        # Without metadata protection 2 ^ 32 = 34 skips the 32-byte middle
        # record; its successor would then pass a raw-only CRC check.
        bank._data[3] ^= 32
        with self.assertRaises(ValueError):
            bank.read_into(1, out)
        bank._data[3] ^= 32
        for offset in range(10):  # Every bit in the first header and payload.
            for bit in range(8):
                bank._data[offset] ^= 1 << bit
                try:
                    with self.subTest(offset=offset, bit=bit):
                        with self.assertRaises(ValueError):
                            bank.read_into(2, out)
                finally:
                    bank._data[offset] ^= 1 << bit
        self.assertEqual(bank.read_into(2, out), 128)
        self.assertEqual(out, b"c" * 128)

    def test_pending_header_alias_is_copied_before_header_write(self):
        for packed in (False, True):
            bank = Bank(136, packed)
            bank._data[:16] = bytes(range(16))
            page = memoryview(bank._data)[:16]
            expected = bytes(page)
            self.assertTrue(bank.append(page))
            out = bytearray(16)
            self.assertEqual(bank.read_into(0, out), 16)
            self.assertEqual(out, expected)
            self.assertEqual(len(bank._data), 136)

    def test_clear_and_reuse(self):
        bank = Bank()
        bank.append(b"a" * 128)
        identity = id(bank._data)
        bank.clear()
        self.assertEqual((bank.used, bank.count, bank.logical_bytes), (0, 0, 0))
        with self.assertRaises(IndexError):
            bank.read_into(0, bytearray(128))
        bank.append(b"new")
        out = bytearray(128)
        self.assertEqual(bank.read_into(0, out), 3)
        self.assertEqual(out[:3], b"new")
        self.assertEqual(id(bank._data), identity)


if __name__ == "__main__":
    unittest.main()
