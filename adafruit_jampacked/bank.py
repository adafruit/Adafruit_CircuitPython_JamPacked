# SPDX-FileCopyrightText: Copyright (c) 2026 Adafruit Industries
# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""
`adafruit_jampacked.bank`
================================================================================

Fixed-size store of packed pages. Each record is an 8-byte header (marker,
mode, raw length, payload length, CRC32) followed by its payload, the jam
packer v0.6 record format. The bytearray is allocated once and never grows.
"""

from adafruit_jampacked.codec import DELTA_RICE, PAGE_SIZE, RAW, RLE, Codec, _byte_view, crc32

__version__ = "0.0.0+auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_CircuitPython_JamPacked.git"

HEADER_SIZE = 8
MARKER = 0xA1  # v0.6 record format version 1
SNAPSHOT_MAGIC = b"JPK1"
SNAPSHOT_HEADER = 16  # magic, used, count, dropped_bytes (little-endian uint32 each)


def _u32(buf, offset):
    return buf[offset] | buf[offset + 1] << 8 | buf[offset + 2] << 16 | buf[offset + 3] << 24


def _put_u32(buf, offset, value):
    for byte in range(4):
        buf[offset + byte] = (value >> (8 * byte)) & 255


class Bank:
    """Append-only (or ring) store of packed 1..128 byte pages.

    :param int capacity: bytes of record storage, allocated once
    :param bool packed: pack pages; ``False`` stores them raw for comparison
    :param bool ring: when full, drop the oldest records instead of refusing

    ``capacity`` counts the bytearray only. The codec scratch and Python
    objects are extra. There is no per-record index, so ``read_into`` scans
    the records before it; use ``pages`` to read everything in one pass.
    """

    def __init__(self, capacity=4096, packed=True, ring=False):
        if not isinstance(capacity, int) or capacity < HEADER_SIZE + 1:
            raise ValueError("capacity must fit a nonempty record")
        if ring and capacity < HEADER_SIZE + PAGE_SIZE:
            raise ValueError("ring capacity must fit a full-page record")
        self.capacity = capacity
        self.packed = bool(packed)
        self.ring = bool(ring)
        self._data = bytearray(capacity)
        self._view = memoryview(self._data)
        self.codec = Codec()
        self.used = 0
        self.logical_bytes = 0
        self.count = 0
        self.dropped_bytes = 0
        """Unpacked bytes dropped by ring mode since the last ``clear``."""

    @property
    def header_bytes(self):
        """Bytes used by record headers."""
        return self.count * HEADER_SIZE

    @property
    def payload_bytes(self):
        """Bytes used by packed payloads."""
        return self.used - self.header_bytes

    @property
    def scratch_bytes(self):
        """Bytes of codec scratch, outside ``capacity``."""
        return self.codec.scratch_bytes

    @property
    def ratio(self):
        """Raw size (with headers) over stored size, as the v0.6 demo reports it."""
        if not self.used:
            return 1.0
        return (self.logical_bytes + self.header_bytes) / self.used

    def clear(self):
        """Forget all records. The allocation is reused; old bytes are not erased."""
        self.used = 0
        self.logical_bytes = 0
        self.count = 0
        self.dropped_bytes = 0

    def append(self, page):
        """Pack and store one page of 1..128 bytes.

        Returns ``False`` without changing anything when the record does not
        fit and ``ring`` is off. With ``ring`` on, the oldest records are
        dropped to make room.
        """
        src = _byte_view(page)
        raw_len = len(src)
        if raw_len < 1 or raw_len > PAGE_SIZE:
            raise ValueError("record must contain 1..128 bytes")
        if self.packed:
            mode, payload = self.codec.encode(src)
        else:
            mode, payload = RAW, bytes(src)
        payload_len = len(payload)
        size = HEADER_SIZE + payload_len
        if self.used + size > self.capacity:
            if not self.ring:
                return False
            self._drop_until(self.capacity - size)
        metadata = bytes((MARKER, mode, raw_len, payload_len))
        checksum = crc32(payload, crc32(metadata))
        offset = self.used
        data = self._data
        self._view[offset + HEADER_SIZE : offset + size] = payload
        data[offset] = MARKER
        data[offset + 1] = mode
        data[offset + 2] = raw_len
        data[offset + 3] = payload_len
        _put_u32(data, offset + 4, checksum)
        self.used = offset + size
        self.logical_bytes += raw_len
        self.count += 1
        return True

    def _drop_until(self, limit):
        # Find how many leading records to drop so that used <= limit.
        cut = 0
        lost = 0
        records = 0
        data = self._data
        while self.used - cut > limit:
            payload_len = data[cut + 3]
            lost += data[cut + 2]
            cut += HEADER_SIZE + payload_len
            records += 1
        # Shift the survivors down. Chunks no longer than the shift never
        # overlap, and copying forward never reads bytes already written.
        view = self._view
        remaining = self.used - cut
        step = min(cut, PAGE_SIZE)
        for start in range(0, remaining, step):
            end = min(start + step, remaining)
            view[start:end] = view[cut + start : cut + end]
        self.used = remaining
        self.count -= records
        self.logical_bytes -= lost
        self.dropped_bytes += lost

    def _header(self, offset):
        data = self._data
        if offset < 0 or offset + HEADER_SIZE > self.used:
            raise ValueError("truncated record header")
        if data[offset] != MARKER:
            raise ValueError("invalid record marker/version")
        mode = data[offset + 1]
        raw_len = data[offset + 2]
        payload_len = data[offset + 3]
        if mode not in {RAW, DELTA_RICE, RLE}:
            raise ValueError("invalid record mode")
        if raw_len < 1 or raw_len > PAGE_SIZE or payload_len < 1:
            raise ValueError("invalid record length")
        if mode == RAW:
            if payload_len != raw_len:
                raise ValueError("raw record length mismatch")
        elif payload_len >= raw_len:
            raise ValueError("packed record did not shrink")
        if offset + HEADER_SIZE + payload_len > self.used:
            raise ValueError("truncated record payload")
        view = self._view
        actual = crc32(view[offset : offset + 4])
        actual = crc32(view[offset + HEADER_SIZE : offset + HEADER_SIZE + payload_len], actual)
        if actual != _u32(data, offset + 4):
            raise ValueError("record CRC32 mismatch")
        return mode, raw_len, payload_len

    def read_into(self, index, out):
        """Unpack record ``index`` into ``out``. Returns its length in bytes.

        Checks the CRC of every record before it while walking to it.
        """
        if not isinstance(index, int) or index < 0 or index >= self.count:
            raise IndexError("record index out of range")
        offset = 0
        for _ in range(index):
            _, _, payload_len = self._header(offset)
            offset += HEADER_SIZE + payload_len
        mode, raw_len, payload_len = self._header(offset)
        start = offset + HEADER_SIZE
        self.codec.decode_into(mode, self._view[start : start + payload_len], out, raw_len)
        return raw_len

    def pages(self, out):
        """Unpack every record, oldest first, into ``out``; yields each length.

        ``out`` is reused for every record, so copy what you need before the
        next step. One pass over the store, unlike repeated ``read_into``.
        """
        offset = 0
        for _ in range(self.count):
            mode, raw_len, payload_len = self._header(offset)
            start = offset + HEADER_SIZE
            self.codec.decode_into(mode, self._view[start : start + payload_len], out, raw_len)
            offset = start + payload_len
            yield raw_len

    def snapshot_size(self):
        """Bytes ``save`` needs for the current contents."""
        return SNAPSHOT_HEADER + self.used

    def save(self, mem, offset=0):
        """Copy the bank into ``mem`` (a bytearray, ``alarm.sleep_memory`` or
        ``microcontroller.nvm``) at ``offset``. Returns the bytes written.

        Uses two slice assignments, so nvm sees two writes, not one per byte.
        The records are copied once into a temporary ``bytes``, because
        ``sleep_memory`` and ``nvm`` accept bytes, bytearray or array only.
        """
        size = self.snapshot_size()
        if offset < 0 or offset + size > len(mem):
            raise ValueError("snapshot does not fit")
        header = bytearray(SNAPSHOT_HEADER)
        header[0:4] = SNAPSHOT_MAGIC
        _put_u32(header, 4, self.used)
        _put_u32(header, 8, self.count)
        _put_u32(header, 12, self.dropped_bytes)
        if self.used:
            mem[offset + SNAPSHOT_HEADER : offset + size] = bytes(self._view[: self.used])
        mem[offset : offset + SNAPSHOT_HEADER] = header
        return size

    def load(self, mem, offset=0):
        """Replace the contents with a snapshot from ``save``.

        Every record's CRC is checked first. Returns ``False`` and leaves the
        bank empty when ``mem`` holds no valid snapshot.
        """
        self.clear()
        if offset < 0 or offset + SNAPSHOT_HEADER > len(mem):
            return False
        header = bytes(mem[offset : offset + SNAPSHOT_HEADER])
        if header[0:4] != SNAPSHOT_MAGIC:
            return False
        used = _u32(header, 4)
        count = _u32(header, 8)
        if used > self.capacity or offset + SNAPSHOT_HEADER + used > len(mem):
            return False
        if used:
            start = offset + SNAPSHOT_HEADER
            self._view[:used] = mem[start : start + used]
        self.used = used
        logical = 0
        cursor = 0
        try:
            for _ in range(count):
                _, raw_len, payload_len = self._header(cursor)
                cursor += HEADER_SIZE + payload_len
                logical += raw_len
        except ValueError:
            self.used = 0
            return False
        if cursor != used:
            self.used = 0
            return False
        self.count = count
        self.logical_bytes = logical
        self.dropped_bytes = _u32(header, 12)
        return True
