# adafruit_jampacked design

Bundle library form of jam packer v0.6. Lossless packing of sensor readings
into 128-byte pages, so 3-5x more smooth readings fit in the same RAM.

## goals

- Same page and record format as v0.6, byte for byte. A v0.6 reader can read
  what this library writes, and the reverse.
- Pure Python first, so it runs on every CircuitPython board and on Blinka.
  A prebuilt viper kernel speeds it up on CircuitPython 11, never required.
- One easy object for the common case: append numbers, read them back.
- Fixed memory. Nothing grows after construction.

## non-goals

- Not a filesystem, not general compression (use zlib/deflate for text files).
- No floats in the stored format. Floats are scaled to integers on the way in.
- No interleaved multi-field records. Measured 1.05x; one stream per reading.

## package layout

```
adafruit_jampacked/
    __init__.py     version only, so importing a submodule stays cheap
    codec.py        page codec, from v0.6 jamcodec.py (format unchanged)
    codec_kernel.<arch>.mpy   turbo (viper) kernel, prebuilt per arch
    bank.py         record bank, from v0.6 jamstore.py, plus ring mode,
                    page iteration and save/load
    samplelog.py    SampleLog: numbers in, numbers out
examples/
    jampacked_simpletest.py     log readings, print ratio, read back
    jampacked_adafruitio.py     hold readings offline, send_batch_data later
    jampacked_sleep_memory.py   keep readings across deep sleep (ESP32)
    jampacked_sparkline.py      long history for a Sparkline
src/jampacked_turbo.py, Makefile  kernel source and per-arch build
tests/                          CPython pytest, incl. v0.6 equivalence
```

## API

### codec

```python
from adafruit_jampacked.codec import Codec, RAW, DELTA_RICE, RLE, PAGE_SIZE
codec = Codec()
mode, payload = codec.encode(page)              # page: 0..128 bytes
codec.decode_into(mode, payload, out, raw_len)  # returns raw_len
codec.ENGINE   # "viper" or "Python"
PythonCodec()  # kernel off
```

Identical output to v0.6. `Codec(turbo=True)` uses the kernel when it loaded.

### Bank

```python
from adafruit_jampacked.bank import Bank
bank = Bank(capacity=4096, ring=False)
bank.append(page) -> bool   # False when full (ring=False)
bank.read_into(i, out) -> int
for raw_len in bank.pages(out): ...   # one pass, O(total), not O(n^2)
bank.count, bank.used, bank.logical_bytes, bank.dropped, bank.ratio
bank.save(mem) / Bank.load(mem)       # mem: bytearray, alarm.sleep_memory, nvm
```

- Records are the v0.6 eight-byte header plus payload, unchanged.
- `ring=True`: when a page does not fit, the oldest records are dropped and
  the rest shifted down. `dropped` counts dropped records. Shift uses a fixed
  128-byte scratch, no allocation.
- `pages(out)` walks records once, checking each CRC, decoding into `out`.
- `save(mem)` writes a 12-byte snapshot header (`b"JPK1"`, used, count,
  dropped) and the used bytes in one slice assignment each, which suits
  `alarm.sleep_memory` and `microcontroller.nvm` (slice assignment, exact
  lengths, every nvm write is an erase cycle). `load` checks the header and
  every record CRC before trusting it.

### SampleLog

```python
from adafruit_jampacked.samplelog import SampleLog
log = SampleLog(capacity=4096, scale=100, offset=0, signed=True, ring=True)
log.append(21.37)          # stored as round(21.37*100) = 2137
len(log)                   # readings held, including the unpacked tail
for value in log: ...      # oldest first, back as 21.37
log.ratio                  # (logical + headers) / used, like the demo
log.flush()                # pack a partial page now (before deep sleep)
log.dropped                # readings lost to ring mode
log.save(mem) / SampleLog.load(mem, ...)
log.clear()
```

- 64 readings per page, buffered in one 128-byte bytearray, packed when full.
- `stored = round(value * scale) + offset`, then checked against the 16-bit
  range (0..65535, or -32768..32767 with `signed=True`). Out of range raises
  `ValueError`; nothing is clipped silently.
- Values come back as `(stored - offset) / scale`; int when `scale == 1`.
- Timestamps are not stored. Fixed-interval logging gives index -> time:
  `t0 + (log.dropped + i) * interval`.

## turbo kernel (viper), replaces the v0.6 native C module

- `src/jampacked_turbo.py`: five `@micropython.viper` functions, the hot loops
  of the codec: `runs`, `numeric_escapes`, `rice_costs` (exact bit counts for
  all 16 k in one pass), `rice_write`, `rice_read` (error codes map to the
  same ValueError messages as Python). Mode choice stays in Python, so the
  tie rule is the same code on both paths.
- Prebuilt per arch with `mpy-cross -march` into
  `adafruit_jampacked/codec_kernel.<arch>.mpy` (armv6m, armv7emsp,
  xtensawin, rv32imc), the FLAC pattern. The bundle build copies non-.py
  package files as-is.
- CircuitPython 11 loads native code by default on raspberrypi, atmel-samd,
  nordic, espressif and stm F4 and picks `name.<arch>.mpy`. 9.x/10.x stock
  builds do not, so the Python path stays complete.
- No C toolchain, no natmod ABI pinning; only the .mpy version (6.3) has to
  match.

## tests

- host: v0.6 tests ported; byte-for-byte equivalence of encode output against
  the original v0.6 jamcodec.py on fuzzed and real sensor pages; ring drop,
  save/load round trip, corrupted snapshot rejected, scale range errors.
- device: Fruit Jam on bravo, MS8607 live readings through SampleLog, ratio and
  exact recovery, Python and native engines.

## verified 2026-10-28, first version (v0.6 native C, since removed)

- host: 33 tests pass (17 ported from v0.6, 16 new); encode output equals
  v0.6 on 71 golden pages (real sensor, fuzz, text, short pages, all modes).
- MS8607 temperature log through SampleLog: 4.38x, same as the v0.6 analysis.
- pre-commit (ruff, reuse) clean; sphinx -W builds; circuitpython-build-bundles
  builds 9.x/10.x/11.x mpy zips (lib about 8.7 KB as .mpy).
- Fruit Jam on bravo, 11.0.0-alpha.2, 11.x mpy build of the library:
  golden 71/71 on Python and native; live MS8607 air temp at 10 Hz, 624
  readings exact, 4.33x; Python encode 19.0 ms / decode 2.9 ms per page,
  native 95 us / 102 us; ring mode exact; snapshot to alarm.sleep_memory
  (124 of 256 bytes) loads back, cleared snapshot rejected; native found
  as `adafruit_jampacked.codec_native` and as `_jamcodec_native`;
  simpletest runs (chip temp 1.61x). Test files removed afterwards and the
  stock app confirmed (`JAM_COMPLETE ... 188 ... 8184`).

## verified, viper kernel (2026-10-28)

- host: 36 tests pass, including the kernel source run under CPython against
  the Python path: 71 golden pages, 3000 fuzz pages, and identical error
  messages on 403 corrupted payloads.
- Fruit Jam, from a clean boot, kernel `codec_kernel.armv7emsp.mpy` loaded:
  golden 71/71 on Python and viper; live MS8607 air temp, 623 readings exact,
  5.12x; per real page Python 17.1 ms pack / 2.5 ms unpack, viper 0.60 ms /
  0.39 ms; mixed golden pages 15.6 ms vs 1.0 ms; ring and sleep_memory pass.
- One earlier run on the same board was about 25x slow everywhere (a plain
  10k-iteration loop took 541 ms instead of 68 ms) after files were copied
  onto CIRCUITPY. Cause unknown; a power cycle cleared it and it did not come
  back in the clean runs.

## open questions for review

1. Name: `adafruit_jampacked` vs `adafruit_jampacker`.
2. Kernel built with mpy-cross 11.0.0-alpha.1; rebuild from the release
   tree before shipping.
3. Author line for the codec files: v0.6 says (c) Adafruit Industries.
