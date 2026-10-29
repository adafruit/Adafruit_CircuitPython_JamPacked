# SPDX-FileCopyrightText: Copyright (c) 2026 Adafruit Industries
# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""
`adafruit_jampacked.codec`
================================================================================

Bounded, lossless codec for pages of 0 to 128 bytes.

Each page is packed on its own, so any page can be read back without the
others. Modes are raw, delta/Rice over little-endian 16-bit samples, and runs
(including a numeric-text subtype). The format is the jam packer v0.6 format.

The hot loops run in a turbo (``@micropython.viper``) kernel when the
prebuilt ``codec_kernel.<arch>.mpy`` for the board loads, and in plain Python
otherwise. Both produce the same bytes.
"""

from array import array

__version__ = "0.0.0+auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_CircuitPython_JamPacked.git"

PAGE_SIZE = 128
RAW = 0
DELTA_RICE = 1
RLE = 2
MODE_NAMES = ("raw", "delta/rice", "runs/text")
_NUMERIC_INTERNAL = 3

# Turbo (viper) kernel, prebuilt per chip as codec_kernel.<arch>.mpy from
# src/jampacked_turbo.py. Missing on Blinka and on firmware without native code.
try:
    from adafruit_jampacked import codec_kernel as _kernel
except (ImportError, ValueError):
    _kernel = None
ENGINE = "Python" if _kernel is None else "viper"
"""``"viper"`` when the turbo kernel loaded, else ``"Python"``."""
_RICE_ERRORS = (
    "truncated rice quotient",
    "rice value exceeds 16 bits",
    "truncated rice remainder",
    "trailing rice bytes",
    "nonzero rice padding",
)

try:
    from binascii import crc32 as _native_crc32
except ImportError:
    _native_crc32 = None


def _crc32_fallback(data, crc=0):
    """CRC-32/ISO-HDLC, including the usual initial/final XOR."""
    crc ^= 0xFFFFFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def crc32(data, crc=0):
    """CRC-32/ISO-HDLC of ``data``, continuing from ``crc``."""
    if _native_crc32 is not None:
        return _native_crc32(data, crc) & 0xFFFFFFFF
    return _crc32_fallback(data, crc) & 0xFFFFFFFF


def _byte_view(data):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("expected a byte buffer")
    result = memoryview(data)
    if getattr(result, "itemsize", 1) != 1 or getattr(result, "format", "B") != "B":
        raise TypeError("expected unsigned byte-sized elements")
    if getattr(result, "ndim", 1) != 1:
        raise TypeError("expected a one-dimensional byte buffer")
    if not getattr(result, "contiguous", True):
        raise TypeError("expected a contiguous byte buffer")
    return result


def _delta(src, offset, previous):
    value = src[offset] | (src[offset + 1] << 8)
    delta = ((value - previous + 32768) & 65535) - 32768
    zigzag = delta * 2 if delta >= 0 else -delta * 2 - 1
    return value, zigzag


def _numeric_code(value):
    if 48 <= value <= 57:
        return value - 48
    if value == 44:
        return 10
    if value == 46:
        return 11
    if value == 45:
        return 12
    if value == 10:
        return 13
    if value == 32:
        return 14
    return 15


def _numeric_nibble(src, at):
    return (src[at >> 1] >> (0 if at & 1 else 4)) & 15


class Codec:
    """One fixed scratch buffer. Returned payload is an independent small bytes.

    encode accepts 0..128 bytes. decode_into never allocates an output buffer;
    the caller supplies one. An invalid decode may modify that output before
    raising ValueError, so callers must not consume it after an exception.

    :param bool turbo: use the turbo kernel when it is available
    """

    def __init__(self, turbo=True):
        self._scratch = bytearray(PAGE_SIZE)
        self.scratch_bytes = PAGE_SIZE
        self._kernel = _kernel if turbo else None
        self._costs = array("i", [0] * 16)  # Rice costs filled by the kernel

    def encode(self, page):
        """Pack 0..128 bytes. Returns ``(mode, payload)``; payload is never longer."""
        src = _byte_view(page)
        size = len(src)
        if size > PAGE_SIZE:
            raise ValueError("page exceeds 128 bytes")
        mode = RAW
        best_size = size
        kernel = self._kernel

        # First count runs; random data never creates a 256-byte candidate.
        if kernel is not None:
            runs = kernel.runs(src, size)
        else:
            runs = 0
            offset = 0
            while offset < size:
                value = src[offset]
                offset += 1
                while offset < size and src[offset] == value:
                    offset += 1
                runs += 1
        if runs * 2 < best_size:
            mode = RLE
            best_size = runs * 2

        # A zero-count RLE prefix reserves the numeric extension.
        numeric_size = 2 + (size + 1) // 2
        if numeric_size < best_size:
            if kernel is not None:
                numeric_size += kernel.numeric_escapes(src, size)
            else:
                for value in src:
                    numeric_size += _numeric_code(value) == 15
                    if numeric_size >= best_size:
                        break
            if numeric_size < best_size:
                mode = _NUMERIC_INTERNAL
                best_size = numeric_size

        # The signed modular difference preserves wraparound exactly. A Rice
        # parameter is selected by exact encoded length, not guessed entropy.
        best_k = 0
        if size >= 4 and size % 2 == 0 and kernel is not None:
            # Same choice as below: exact costs, strictly smaller, lowest k wins.
            costs = self._costs
            kernel.rice_costs(src, size, costs)
            for k in range(16):
                candidate_size = 3 + ((costs[k] + 7) // 8)
                if candidate_size < best_size:
                    mode = DELTA_RICE
                    best_k = k
                    best_size = candidate_size
        elif size >= 4 and size % 2 == 0:
            for k in range(16):
                bits = 0
                previous = src[0] | (src[1] << 8)
                for offset in range(2, size, 2):
                    previous, zigzag = _delta(src, offset, previous)
                    bits += (zigzag >> k) + 1 + k
                    # No later symbols can make this candidate shorter.
                    if 3 + ((bits + 7) // 8) >= best_size:
                        break
                candidate_size = 3 + ((bits + 7) // 8)
                if candidate_size < best_size:
                    mode = DELTA_RICE
                    best_k = k
                    best_size = candidate_size

        if mode == RAW:
            return RAW, bytes(src)

        scratch = self._scratch
        if mode == _NUMERIC_INTERNAL:
            scratch[0] = 0
            scratch[1] = 1
            pos = 2
            half = 0
            for value in src:
                code = _numeric_code(value)
                if not half:
                    if code == 15:
                        scratch[pos] = 0xF0 | (value >> 4)
                        pos += 1
                        scratch[pos] = (value << 4) & 255
                    else:
                        scratch[pos] = code << 4
                else:
                    scratch[pos] |= code
                    pos += 1
                    if code == 15:
                        scratch[pos] = value
                        pos += 1
                half ^= 1
            return RLE, bytes(memoryview(scratch)[:best_size])
        if mode == RLE:
            offset = 0
            written = 0
            while offset < size:
                start = offset
                value = src[offset]
                offset += 1
                while offset < size and src[offset] == value:
                    offset += 1
                scratch[written] = offset - start
                scratch[written + 1] = value
                written += 2
        elif kernel is not None:
            kernel.rice_write(src, size, best_k, scratch)
        else:
            for offset in range(best_size):
                scratch[offset] = 0
            scratch[0] = src[0]
            scratch[1] = src[1]
            scratch[2] = best_k
            bitpos = 24
            previous = src[0] | (src[1] << 8)
            for offset in range(2, size, 2):
                previous, zigzag = _delta(src, offset, previous)
                bitpos += zigzag >> best_k  # Unary zeroes are already clear.
                scratch[bitpos >> 3] |= 1 << (7 - (bitpos & 7))
                bitpos += 1
                for bit in range(best_k - 1, -1, -1):
                    if zigzag & (1 << bit):
                        scratch[bitpos >> 3] |= 1 << (7 - (bitpos & 7))
                    bitpos += 1
        return mode, bytes(memoryview(scratch)[:best_size])

    def decode_into(self, mode, payload, out, raw_len):
        """Unpack ``payload`` into ``out``. Returns ``raw_len``; raises ValueError if invalid."""
        src = _byte_view(payload)
        dst = _byte_view(out)
        if not isinstance(raw_len, int) or raw_len < 0 or raw_len > PAGE_SIZE:
            raise ValueError("invalid raw length")
        if len(dst) < raw_len:
            raise ValueError("output buffer too small")
        if len(src) > PAGE_SIZE:
            raise ValueError("payload exceeds 128 bytes")
        if mode == RAW:
            if len(src) != raw_len:
                raise ValueError("raw payload length mismatch")
            for offset in range(raw_len):
                dst[offset] = src[offset]
            return raw_len
        if mode == RLE and len(src) and src[0] == 0:
            if raw_len == 0 or len(src) < 2 or src[1] != 1:
                raise ValueError("invalid numeric header")
            alphabet = b"0123456789,.-\n "
            at = 4
            limit = 2 * len(src)
            for offset in range(raw_len):
                if at >= limit:
                    raise ValueError("truncated numeric symbol")
                code = _numeric_nibble(src, at)
                at += 1
                if code < 15:
                    dst[offset] = alphabet[code]
                else:
                    if limit - at < 2:
                        raise ValueError("truncated numeric escape")
                    value = 16 * _numeric_nibble(src, at) + _numeric_nibble(src, at + 1)
                    at += 2
                    if _numeric_code(value) != 15:
                        raise ValueError("noncanonical numeric escape")
                    dst[offset] = value
            if (at + 1) // 2 != len(src):
                raise ValueError("trailing numeric bytes")
            if at & 1 and src[-1] & 15:
                raise ValueError("nonzero numeric padding")
            return raw_len
        if mode == RLE:
            if raw_len == 0 or len(src) == 0 or len(src) % 2:
                raise ValueError("invalid run payload")
            written = 0
            for offset in range(0, len(src), 2):
                count = src[offset]
                if count == 0 or written + count > raw_len:
                    raise ValueError("invalid run length")
                for _ in range(count):
                    dst[written] = src[offset + 1]
                    written += 1
            if written != raw_len:
                raise ValueError("run output length mismatch")
            return written
        if mode != DELTA_RICE:
            raise ValueError("unknown codec mode")
        if raw_len < 4 or raw_len % 2 or len(src) < 4 or src[2] > 15:
            raise ValueError("invalid delta/rice header")
        if self._kernel is not None:
            result = self._kernel.rice_read(src, len(src), dst, raw_len)
            if result < 0:
                raise ValueError(_RICE_ERRORS[-1 - result])
            return raw_len
        k = src[2]
        previous = src[0] | (src[1] << 8)
        dst[0] = src[0]
        dst[1] = src[1]
        bitpos = 24
        bitlimit = len(src) * 8
        for offset in range(2, raw_len, 2):
            quotient = 0
            while True:
                if bitpos >= bitlimit:
                    raise ValueError("truncated rice quotient")
                bit = (src[bitpos >> 3] >> (7 - (bitpos & 7))) & 1
                bitpos += 1
                if bit:
                    break
                quotient += 1
                if quotient > (65535 >> k):
                    raise ValueError("rice value exceeds 16 bits")
            if bitpos + k > bitlimit:
                raise ValueError("truncated rice remainder")
            zigzag = quotient << k
            for bit in range(k - 1, -1, -1):
                zigzag |= ((src[bitpos >> 3] >> (7 - (bitpos & 7))) & 1) << bit
                bitpos += 1
            delta = (zigzag >> 1) if not zigzag & 1 else -(zigzag >> 1) - 1
            previous = (previous + delta) & 65535
            dst[offset] = previous & 255
            dst[offset + 1] = previous >> 8
        if (bitpos + 7) // 8 != len(src):
            raise ValueError("trailing rice bytes")
        while bitpos < bitlimit:
            if src[bitpos >> 3] & (1 << (7 - (bitpos & 7))):
                raise ValueError("nonzero rice padding")
            bitpos += 1
        return raw_len


class PythonCodec(Codec):
    """The codec with the turbo kernel off: plain Python on every board."""

    def __init__(self):
        super().__init__(turbo=False)
