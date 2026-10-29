# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""
`adafruit_jampacked`
================================================================================

Lossless packing of sensor readings in fixed RAM, so more history fits.

* Author(s): Adafruit Industries, Mikey Sklar

Submodules, imported on their own so you only load what you use:

* :mod:`adafruit_jampacked.samplelog`: numbers in, numbers out
* :mod:`adafruit_jampacked.bank`: fixed-size store of packed pages
* :mod:`adafruit_jampacked.codec`: the 128-byte page codec

Implementation Notes
--------------------

**Software and Dependencies:**

* Adafruit CircuitPython firmware for the supported boards:
  https://circuitpython.org/downloads
* No other libraries. A prebuilt turbo kernel is used on CircuitPython 11 when it loads.
"""

__version__ = "0.0.0+auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_CircuitPython_JamPacked.git"
