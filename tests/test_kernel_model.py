# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""Run src/jampacked_turbo.py under CPython and check it against the Python path.

The viper decorator becomes a no-op, so the kernel's logic is tested on the
host. The compiled kernel is checked on a board by the same golden pages.
"""

import random
import struct
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_jampacked import golden

from adafruit_jampacked.codec import DELTA_RICE, Codec, PythonCodec


def load_model():
    stub = types.ModuleType("micropython")
    stub.viper = lambda function: function
    sys.modules.setdefault("micropython", stub)
    model = types.ModuleType("codec_kernel_model")
    for name in ("ptr8", "ptr32", "uint"):
        setattr(model, name, object)
    source = (ROOT / "src" / "jampacked_turbo.py").read_text()
    exec(compile(source, "jampacked_turbo.py", "exec"), model.__dict__)
    return model


def turbo_codec():
    codec = Codec(turbo=False)
    codec._kernel = load_model()
    return codec


class KernelModelTests(unittest.TestCase):
    def test_golden_pages(self):
        codec = turbo_codec()
        out = bytearray(128)
        for page, mode, payload in golden():
            self.assertEqual(codec.encode(page), (mode, payload))
            self.assertEqual(codec.decode_into(mode, payload, out, len(page)), len(page))
            self.assertEqual(bytes(out[: len(page)]), page)

    def test_fuzz_matches_python(self):
        rng = random.Random(11)
        turbo, plain = turbo_codec(), PythonCodec()
        out = bytearray(128)
        for _ in range(3000):
            size = rng.randrange(0, 129)
            spread = rng.choice((0, 1, 3, 40, 1000, 65536))
            base = rng.randrange(65536)
            words = [(base + rng.randrange(-spread, spread + 1)) & 0xFFFF for _ in range(64)]
            page = struct.pack("<64H", *words)[:size]
            got = turbo.encode(page)
            self.assertEqual(got, plain.encode(page))
            turbo.decode_into(got[0], got[1], out, size)
            self.assertEqual(bytes(out[:size]), page)

    def test_malformed_rice_errors_match_python(self):
        rng = random.Random(12)
        turbo, plain = turbo_codec(), PythonCodec()
        page = struct.pack("<64H", *[1000 + rng.randrange(50) for _ in range(64)])
        mode, payload = plain.encode(page)
        self.assertEqual(mode, DELTA_RICE)
        cases = [payload[:-1], payload + b"\0", payload[:3] + b"\0" * (len(payload) - 3)]
        for _ in range(400):
            bad = bytearray(payload)
            bad[rng.randrange(3, len(bad))] ^= 1 << rng.randrange(8)
            cases.append(bytes(bad))
        for bad in cases:
            results = []
            for codec in (turbo, plain):
                try:
                    codec.decode_into(mode, bad, bytearray(128), len(page))
                    results.append("ok")
                except ValueError as error:
                    results.append(str(error))
            self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
