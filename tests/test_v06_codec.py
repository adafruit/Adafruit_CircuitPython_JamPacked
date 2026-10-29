# SPDX-FileCopyrightText: Copyright (c) 2026 Adafruit Industries
# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""Ported from the jam packer v0.6 host tests."""

import array
import binascii
import random
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adafruit_jampacked import codec as jamcodec


def reference_decode(mode, data, raw_len):
    """Deliberately different, allocation-heavy host oracle."""
    if mode == jamcodec.RAW:
        return bytes(data)
    if mode == jamcodec.RLE and data[:2] == b"\x00\x01":
        alphabet = b"0123456789,.-\n "
        bits = "".join(format(value, "08b") for value in data[2:])
        output = bytearray()
        while len(output) < raw_len:
            code = int(bits[:4], 2)
            bits = bits[4:]
            if code == 15:
                output.append(int(bits[:8], 2))
                bits = bits[8:]
            else:
                output.append(alphabet[code])
        assert len(bits) < 8 and not bits.strip("0")
        return bytes(output)
    if mode == jamcodec.RLE:
        return b"".join(bytes([data[i + 1]]) * data[i] for i in range(0, len(data), 2))
    first, k = struct.unpack("<HB", data[:3])
    bits = "".join(format(value, "08b") for value in data[3:])
    values = [first]
    while len(values) < raw_len // 2:
        quotient = bits.index("1")
        bits = bits[quotient + 1 :]
        remainder = int(bits[:k], 2) if k else 0
        bits = bits[k:]
        code = (quotient << k) + remainder
        signed = code // 2 if code % 2 == 0 else -(code // 2) - 1
        values.append((values[-1] + signed) % 65536)
    return struct.pack("<" + "H" * len(values), *values)


def packed_samples(values):
    return struct.pack("<" + "H" * len(values), *values)


class CodecTests(unittest.TestCase):
    def setUp(self):
        self.codec = jamcodec.Codec()

    def roundtrip(self, page):
        mode, payload = self.codec.encode(page)
        self.assertLessEqual(len(payload), len(page))
        if mode != jamcodec.RAW:
            self.assertLess(len(payload), len(page))
        out = bytearray(b"\xca" * 140)
        self.assertEqual(self.codec.decode_into(mode, payload, out, len(page)), len(page))
        self.assertEqual(bytes(out[: len(page)]), bytes(page))
        self.assertEqual(out[len(page) :], b"\xca" * (140 - len(page)))
        self.assertEqual(reference_decode(mode, payload, len(page)), bytes(page))
        return mode, payload

    def test_all_lengths_and_input_shapes(self):
        for size in range(129):
            self.roundtrip(bytes(size))
            self.roundtrip(bytes(range(size)))
            self.roundtrip(bytearray([42] * size))
            self.roundtrip(memoryview(bytes([7, 19] * 64))[:size])

    def test_signed_boundaries_and_wraparound(self):
        cases = [
            [65532, 65533, 65534, 65535, 0, 1, 2, 3] * 8,
            [0, 65535, 65534, 65533, 65532, 65531, 65530, 65529] * 8,
            [32766, 32767, 32768, 32769] * 16,
            [0, 32768] * 32,
            [65535, 0] * 32,
            [0, 1, 65535, 32767, 32768, 65534, 2, 3] * 8,
        ]
        for values in cases:
            self.roundtrip(packed_samples(values))

    def test_modes_and_known_rice_vector(self):
        self.assertEqual(self.roundtrip(b"z" * 128)[0], jamcodec.RLE)
        self.assertEqual(self.roundtrip(bytes(range(127)))[0], jamcodec.RAW)
        page = packed_samples([1000] * 64)
        mode, payload = self.roundtrip(page)
        self.assertEqual(mode, jamcodec.DELTA_RICE)
        self.assertEqual(payload, b"\xe8\x03\x00" + b"\xff" * 7 + b"\xfe")
        self.assertEqual(self.roundtrip(packed_samples(range(1000, 1064)))[0], jamcodec.DELTA_RICE)

    def test_fuzz_against_independent_decode(self):
        rng = random.Random(104729)
        for _ in range(800):
            size = rng.randrange(129)
            self.roundtrip(bytes(rng.randrange(256) for _ in range(size)))
            previous = rng.randrange(65536)
            values = [previous]
            delta_range = rng.choice([1, 4, 16, 256, 4096, 32768])
            for _ in range(63):
                previous = (previous + rng.randint(-delta_range, delta_range)) % 65536
                values.append(previous)
            self.roundtrip(packed_samples(values))

    def test_selects_smallest_payload_from_available_modes(self):
        rng = random.Random(97)
        for _ in range(100):
            values = [rng.randrange(65536)]
            step = rng.choice([0, 2, 10, 100, 1000])
            for _ in range(63):
                values.append((values[-1] + rng.randint(-step, step)) % 65536)
            raw = packed_samples(values)
            deltas = [((b - a + 32768) % 65536) - 32768 for a, b in zip(values, values[1:])]
            zz = [2 * d if d >= 0 else -2 * d - 1 for d in deltas]
            lengths = [
                len(raw),
                2 + (len(raw) + 1) // 2 + sum(value not in b"0123456789,.-\n " for value in raw),
            ]
            lengths.append(2 * (1 + sum(a != b for a, b in zip(raw, raw[1:]))))
            lengths.extend(3 + (sum((z >> k) + 1 + k for z in zz) + 7) // 8 for k in range(16))
            self.assertEqual(len(self.codec.encode(raw)[1]), min(lengths))

    def test_malformed_inputs(self):
        malformed = [
            (99, b"a", 1),
            (jamcodec.RAW, b"ab", 1),
            (jamcodec.RLE, b"\x00z", 3),
            (jamcodec.RLE, b"\x04z", 3),
            (jamcodec.RLE, b"\x02z", 3),
            (jamcodec.RLE, b"\x02", 2),
            (jamcodec.DELTA_RICE, b"\x00\x00\x10\x80", 4),
            (jamcodec.DELTA_RICE, b"\x00\x00\x00\x00", 4),
            (jamcodec.DELTA_RICE, b"\x00\x00\x0f\x80", 4),
            (jamcodec.DELTA_RICE, b"\x00\x00\x00\x81", 4),
            (jamcodec.DELTA_RICE, b"\x00\x00\x00\x80\x00", 4),
            (jamcodec.DELTA_RICE, b"\x00\x00\x00\x80", 3),
            (jamcodec.DELTA_RICE, b"\x00\x00\x0f\x20\x00", 4),
        ]
        for mode, payload, size in malformed:
            with self.subTest(mode=mode, payload=payload, size=size):
                with self.assertRaises(ValueError):
                    self.codec.decode_into(mode, payload, bytearray(128), size)
        with self.assertRaises(ValueError):
            self.codec.encode(bytes(129))
        with self.assertRaises(ValueError):
            self.codec.decode_into(0, b"ab", bytearray(1), 2)
        with self.assertRaises(ValueError):
            self.codec.decode_into(0, b"", bytearray(128), -1)
        with self.assertRaises(TypeError):
            self.codec.encode("not bytes")
        with self.assertRaises(TypeError):
            self.codec.encode(memoryview(array.array("H", [1, 2])))
        with self.assertRaises(TypeError):
            self.codec.encode(memoryview(array.array("b", [-1, 1])))
        with self.assertRaises(TypeError):
            self.codec.encode(memoryview(b"abcdef")[::2])

    def test_numeric_text_with_literal_and_padding(self):
        for page in [b"1234567890", b"-12.3, 45.6\n" * 9, b"1234X5678", b"01234\x0056789" * 8]:
            mode, payload = self.roundtrip(page)
            self.assertEqual((mode, payload[:2]), (jamcodec.RLE, b"\x00\x01"))
        for payload, raw_len in [
            (b"\x00\x01", 1),
            (b"\x00\x01\x1f", 1),
            (b"\x00\x01\xf3\x10", 1),
            (b"\x00\x01\x12\x00", 2),
        ]:
            with self.assertRaises(ValueError):
                self.codec.decode_into(2, payload, bytearray(128), raw_len)

    def test_crc_matches_standard_and_incremental(self):
        self.assertEqual(jamcodec.crc32(b"123456789"), 0xCBF43926)
        rng = random.Random(31)
        for size in range(129):
            data = bytes(rng.randrange(256) for _ in range(size))
            expected = binascii.crc32(data)
            self.assertEqual(jamcodec._crc32_fallback(data), expected)
            middle = size // 2
            crc = jamcodec._crc32_fallback(data[:middle])
            self.assertEqual(jamcodec._crc32_fallback(data[middle:], crc), expected)


if __name__ == "__main__":
    unittest.main()
