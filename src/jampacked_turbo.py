# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
# SPDX-License-Identifier: MIT
"""jampacked codec hot loops as turbo (@micropython.viper) functions.

Viper ports of the loops in `adafruit_jampacked.codec`. Every function gives
the same result as the plain Python path, so pages are byte-identical either
way. Values stay well inside 32 bits: samples are 16-bit and a page holds at
most 63 deltas, so a Rice cost is at most 63 * 65552 bits.

Compile with: mpy-cross -march=armv7emsp jampacked_turbo.py
"""

import micropython


@micropython.viper
def runs(src: ptr8, n: int) -> int:
    """Number of maximal runs of equal bytes in src[0:n]."""
    count = 0
    i = 0
    while i < n:
        value = src[i]
        i += 1
        while i < n and src[i] == value:
            i += 1
        count += 1
    return count


@micropython.viper
def numeric_escapes(src: ptr8, n: int) -> int:
    """Bytes in src[0:n] outside the numeric alphabet ``0-9 , . - newline space``."""
    count = 0
    i = 0
    while i < n:
        value = src[i]
        if value < 48 or value > 57:
            if value != 44 and value != 45 and value != 46 and value != 10 and value != 32:
                count += 1
        i += 1
    return count


@micropython.viper
def rice_costs(src: ptr8, n: int, costs: ptr32):
    """Exact delta/Rice bit counts for k = 0..15 into costs[0:16].

    src[0:n] holds little-endian 16-bit samples; n is even and at least 4.
    """
    k = 0
    while k < 16:
        costs[k] = 0
        k += 1
    previous = src[0] | (src[1] << 8)
    i = 2
    while i < n:
        value = src[i] | (src[i + 1] << 8)
        delta = ((value - previous + 32768) & 65535) - 32768
        previous = value
        if delta >= 0:
            zigzag = delta << 1
        else:
            zigzag = ((0 - delta) << 1) - 1
        # z >> k adds nothing once it reaches zero
        k = 0
        while zigzag:
            costs[k] = costs[k] + zigzag
            zigzag >>= 1
            k += 1
        i += 2
    symbols = (n >> 1) - 1
    k = 0
    while k < 16:
        costs[k] = costs[k] + symbols * (1 + k)
        k += 1


@micropython.viper
def rice_write(src: ptr8, n: int, k: int, out: ptr8) -> int:
    """Write the delta/Rice payload for src[0:n] with parameter k into out.

    Returns the payload length. out must hold n bytes; the payload is always
    shorter when the caller has chosen this mode.
    """
    i = 0
    while i < n:
        out[i] = 0
        i += 1
    out[0] = src[0]
    out[1] = src[1]
    out[2] = k
    bitpos = 24
    previous = src[0] | (src[1] << 8)
    i = 2
    while i < n:
        value = src[i] | (src[i + 1] << 8)
        delta = ((value - previous + 32768) & 65535) - 32768
        previous = value
        if delta >= 0:
            zigzag = delta << 1
        else:
            zigzag = ((0 - delta) << 1) - 1
        bitpos += zigzag >> k  # unary zeroes are already clear
        at = bitpos >> 3
        out[at] = out[at] | (1 << (7 - (bitpos & 7)))
        bitpos += 1
        bit = k - 1
        while bit >= 0:
            if (zigzag >> bit) & 1:
                at = bitpos >> 3
                out[at] = out[at] | (1 << (7 - (bitpos & 7)))
            bitpos += 1
            bit -= 1
        i += 2
    return (bitpos + 7) >> 3


@micropython.viper
def rice_read(src: ptr8, length: int, out: ptr8, n: int) -> int:
    """Decode a delta/Rice payload src[0:length] into out[0:n].

    The caller has checked the header. Returns n, or a negative error code:
    -1 truncated quotient, -2 value over 16 bits, -3 truncated remainder,
    -4 trailing bytes, -5 nonzero padding.
    """
    k = src[2]
    quotient_limit = 65535 >> k
    previous = src[0] | (src[1] << 8)
    out[0] = src[0]
    out[1] = src[1]
    bitpos = 24
    bitlimit = length << 3
    i = 2
    while i < n:
        quotient = 0
        while True:
            if bitpos >= bitlimit:
                return -1
            bit = (src[bitpos >> 3] >> (7 - (bitpos & 7))) & 1
            bitpos += 1
            if bit:
                break
            quotient += 1
            if quotient > quotient_limit:
                return -2
        if bitpos + k > bitlimit:
            return -3
        zigzag = quotient << k
        bit = k - 1
        while bit >= 0:
            zigzag |= ((src[bitpos >> 3] >> (7 - (bitpos & 7))) & 1) << bit
            bitpos += 1
            bit -= 1
        if zigzag & 1:
            delta = 0 - (zigzag >> 1) - 1
        else:
            delta = zigzag >> 1
        previous = (previous + delta) & 65535
        out[i] = previous & 255
        out[i + 1] = previous >> 8
        i += 2
    if (bitpos + 7) >> 3 != length:
        return -4
    while bitpos < bitlimit:
        if src[bitpos >> 3] & (1 << (7 - (bitpos & 7))):
            return -5
        bitpos += 1
    return n
