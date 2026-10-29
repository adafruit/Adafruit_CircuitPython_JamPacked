# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""
`adafruit_jampacked.samplelog`
================================================================================

Numbers in, numbers out. Readings are scaled to 16-bit integers, collected 64
at a time in a 128-byte page, and packed into a :class:`~adafruit_jampacked.bank.Bank`
when the page fills. Use one log per kind of reading: a stream of one sensor
channel packs well, mixed channels do not.
"""

from adafruit_jampacked.bank import Bank

__version__ = "0.0.0+auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_CircuitPython_JamPacked.git"

SAMPLES_PER_PAGE = 64
_PAGE_BYTES = 2 * SAMPLES_PER_PAGE


class SampleLog:
    """Fixed-memory log of one stream of readings.

    :param int capacity: bytes of packed storage, allocated once
    :param scale: readings are stored as ``round(value * scale) + offset``
    :param int offset: added after scaling, to move readings into range
    :param bool signed: stored range is -32768..32767 instead of 0..65535
    :param bool ring: when full, drop the oldest readings instead of refusing

    A reading outside the 16-bit range after scaling raises ``ValueError``.
    Nothing is clipped. Readings come back as ``(stored - offset) / scale``,
    or as ints when ``scale`` is 1.

    .. code-block:: python

        log = SampleLog(capacity=4096, scale=100, signed=True)
        log.append(21.37)   # stored as 2137
        for celsius in log:
            print(celsius)
    """

    def __init__(self, capacity=4096, scale=1, offset=0, signed=False, ring=False):
        if not scale:
            raise ValueError("scale must be nonzero")
        self.bank = Bank(capacity, ring=ring)
        self.scale = scale
        self.offset = offset
        self.signed = bool(signed)
        self._low = -32768 if self.signed else 0
        self._page = bytearray(_PAGE_BYTES)
        self._out = bytearray(_PAGE_BYTES)
        self._pending = 0

    def _stored(self, value):
        if self.scale != 1 or not isinstance(value, int):
            value = round(value * self.scale)
        value += self.offset
        if not self._low <= value <= self._low + 65535:
            raise ValueError("reading out of 16-bit range after scale/offset")
        return value & 0xFFFF

    def _value(self, word):
        if self.signed and word >= 32768:
            word -= 65536
        word -= self.offset
        if self.scale == 1:
            return word
        return word / self.scale

    def append(self, value):
        """Add one reading. Returns ``False`` if the log is full (ring off).

        A full page is packed on the next append, so a refused reading leaves
        everything already stored unchanged.
        """
        word = self._stored(value)
        if self._pending == SAMPLES_PER_PAGE:
            if not self.bank.append(self._page):
                return False
            self._pending = 0
        at = 2 * self._pending
        self._page[at] = word & 255
        self._page[at + 1] = word >> 8
        self._pending += 1
        return True

    def extend(self, values):
        """Add readings in order. Returns how many were stored."""
        stored = 0
        for value in values:
            if not self.append(value):
                break
            stored += 1
        return stored

    def flush(self):
        """Pack the unfilled page now. Returns ``False`` if it did not fit.

        Not needed before ``save``, which keeps the unfilled page as is. A
        short page packs a little worse than a full one.
        """
        if not self._pending:
            return True
        if not self.bank.append(memoryview(self._page)[: 2 * self._pending]):
            return False
        self._pending = 0
        return True

    def clear(self):
        """Forget all readings."""
        self.bank.clear()
        self._pending = 0

    def __len__(self):
        return self.bank.logical_bytes // 2 + self._pending

    def __iter__(self):
        out = self._out
        for raw_len in self.bank.pages(out):
            for at in range(0, raw_len, 2):
                yield self._value(out[at] | out[at + 1] << 8)
        page = self._page
        for at in range(0, 2 * self._pending, 2):
            yield self._value(page[at] | page[at + 1] << 8)

    @property
    def dropped(self):
        """Readings dropped by ring mode since the last ``clear``."""
        return self.bank.dropped_bytes // 2

    @property
    def ratio(self):
        """Packing ratio of the packed pages (the unfilled page is not counted)."""
        return self.bank.ratio

    @property
    def free(self):
        """Bytes of packed storage left. A page takes 8 to 136 of them."""
        return self.bank.capacity - self.bank.used

    def snapshot_size(self):
        """Bytes ``save`` needs for the current contents."""
        return self.bank.snapshot_size() + 2 + 2 * self._pending

    def save(self, mem, offset=0):
        """Copy the log, unfilled page included, into ``mem``.

        ``mem`` can be a bytearray, ``alarm.sleep_memory`` or
        ``microcontroller.nvm``. Returns the bytes written. Scale, offset and
        signedness are not saved; construct the log the same way to load it.
        """
        size = self.snapshot_size()
        if offset < 0 or offset + size > len(mem):
            raise ValueError("snapshot does not fit")
        at = offset + self.bank.save(mem, offset)
        tail = bytearray(2 + 2 * self._pending)
        tail[0] = self._pending
        tail[1] = 0x5A
        tail[2:] = memoryview(self._page)[: 2 * self._pending]
        mem[at : at + len(tail)] = tail
        return size

    def load(self, mem, offset=0):
        """Replace the contents with a snapshot from ``save``.

        Returns ``False`` and leaves the log empty when ``mem`` holds no valid
        snapshot, for example on the first boot.
        """
        self._pending = 0
        if not self.bank.load(mem, offset):
            return False
        at = offset + self.bank.snapshot_size()
        if at + 2 > len(mem):
            self.bank.clear()
            return False
        tail = bytes(mem[at : at + 2])
        pending = tail[0]
        if tail[1] != 0x5A or pending > SAMPLES_PER_PAGE or at + 2 + 2 * pending > len(mem):
            self.bank.clear()
            return False
        if pending:
            self._page[: 2 * pending] = mem[at + 2 : at + 2 + 2 * pending]
        self._pending = pending
        return True
