# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""Keep a temperature history across deep sleep in alarm.sleep_memory.

For ESP32 boards, where sleep_memory is 2 to 4 KB. RP2040, RP2350 and
nRF52840 have 256 bytes, too little for a ring log.
"""

import time

import alarm
import microcontroller

from adafruit_jampacked.samplelog import SampleLog

SLEEP_SECONDS = 60

mem = alarm.sleep_memory
# The snapshot adds a 16-byte header and up to 130 bytes for the open page.
log = SampleLog(capacity=len(mem) - 146, scale=100, signed=True, ring=True)
if not log.load(mem):
    print("starting a new history")

log.append(microcontroller.cpu.temperature)
print("readings kept:", len(log), "dropped:", log.dropped)
print("ratio: %.2fx" % log.ratio)
log.save(mem)

alarm.exit_and_deep_sleep_until_alarms(
    alarm.time.TimeAlarm(monotonic_time=time.monotonic() + SLEEP_SECONDS)
)
