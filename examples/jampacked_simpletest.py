# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""Log the CPU temperature, then read it all back and show the packing ratio."""

import time

import microcontroller

from adafruit_jampacked.samplelog import SampleLog

# Hundredths of a degree, so 21.37 C is stored as the integer 2137.
log = SampleLog(capacity=2048, scale=100, signed=True)

for _ in range(300):
    log.append(microcontroller.cpu.temperature)
    time.sleep(0.05)

print("readings:", len(log))
print("packed pages ratio: %.2fx" % log.ratio)
print("bytes left:", log.free)
print("last five:", list(log)[-5:])
