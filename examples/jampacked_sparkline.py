# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""Hold a long history in packed form and show the newest part on a Sparkline."""

import time

import board
import displayio
import microcontroller
from adafruit_display_shapes.sparkline import Sparkline

from adafruit_jampacked.samplelog import SampleLog

POINTS = 100  # points on screen

display = board.DISPLAY
log = SampleLog(capacity=4096, scale=100, signed=True, ring=True)
group = displayio.Group()
display.root_group = group
line = None

while True:
    log.append(microcontroller.cpu.temperature)
    if line is not None:
        group.remove(line)
    line = Sparkline(
        width=display.width - 20, height=display.height - 20, max_items=POINTS, x=10, y=10
    )
    skip = len(log) - POINTS
    for index, value in enumerate(log):
        if index >= skip:
            line.add_value(value, update=False)
    line.update()
    group.append(line)
    time.sleep(1)
