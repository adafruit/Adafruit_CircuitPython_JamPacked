Introduction
============


.. image:: https://readthedocs.org/projects/adafruit-circuitpython-jampacked/badge/?version=latest
    :target: https://docs.circuitpython.org/projects/jampacked/en/latest/
    :alt: Documentation Status


.. image:: https://raw.githubusercontent.com/adafruit/Adafruit_CircuitPython_Bundle/main/badges/adafruit_discord.svg
    :target: https://adafru.it/discord
    :alt: Discord


.. image:: https://github.com/adafruit/Adafruit_CircuitPython_JamPacked/workflows/Build%20CI/badge.svg
    :target: https://github.com/adafruit/Adafruit_CircuitPython_JamPacked/actions
    :alt: Build Status


.. image:: https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json
    :target: https://github.com/astral-sh/ruff
    :alt: Code Style: Ruff

Keep more sensor readings in the same RAM. Readings are packed 64 at a time into 128-byte
pages and come back exactly. Slow, smooth readings (temperature, pressure, CO2, PM2.5) pack
3 to 5 times smaller; noisy ones about 2 times.

Each page is packed on its own, so memory stays fixed and nothing grows. Use it to hold
readings while Wi-Fi is down, to log for hours before writing to flash, to keep a long graph
history, or to fit more readings in ``alarm.sleep_memory`` on ESP32 boards.

The page format is the jam packer v0.6 format, byte for byte.


Dependencies
=============
This library depends on:

* `Adafruit CircuitPython <https://github.com/adafruit/circuitpython>`_

It runs on any board in plain Python. On CircuitPython 11 a prebuilt turbo kernel makes packing
about 29 times faster (see below).

Please ensure all dependencies are available on the CircuitPython filesystem.
This is easily achieved by downloading
`the Adafruit library and driver bundle <https://circuitpython.org/libraries>`_
or individual libraries can be installed using
`circup <https://github.com/adafruit/circup>`_.

Installing to a Connected CircuitPython Device with Circup
==========================================================

Make sure that you have ``circup`` installed in your Python environment.
Install it with the following command if necessary:

.. code-block:: shell

    pip3 install circup

With ``circup`` installed and your CircuitPython device connected use the
following command to install:

.. code-block:: shell

    circup install adafruit_jampacked

Or the following command to update an existing version:

.. code-block:: shell

    circup update

Usage Example
=============

.. code-block:: python

    from adafruit_jampacked.samplelog import SampleLog

    # store hundredths of a degree: 21.37 is kept as the integer 2137
    log = SampleLog(capacity=4096, scale=100, signed=True, ring=True)

    log.append(21.37)
    for celsius in log:
        print(celsius)
    print("%.2fx" % log.ratio)

Readings are stored as 16-bit integers: ``round(value * scale) + offset`` must fit in 0..65535,
or -32768..32767 with ``signed=True``. Anything outside raises ``ValueError``; nothing is clipped.

Use one ``SampleLog`` per kind of reading. A stream of one channel packs well. Mixing channels
in one stream packs about 1.05x.

Results
=======

Measured on air-quality sensors, 23 minutes at one reading a second. Ratio includes the 8-byte
page headers.

=====================  ===========  =================
Reading                jampacked    deflate per page
=====================  ===========  =================
PM1                    5.50x        2.60x
MS8607 temperature     4.38x        2.23x
CO2                    4.35x        1.84x
pressure               3.13x        1.76x
VOC raw                1.84x        1.11x
all 17 channels        3.05x        1.68x
=====================  ===========  =================

Turbo kernel
============

The packing loops also come as a turbo (``@micropython.viper``) kernel, prebuilt for each chip
architecture as ``adafruit_jampacked/codec_kernel.<arch>.mpy``. CircuitPython 11 loads the one
that matches the board; elsewhere (CircuitPython 9 and 10, Blinka, or a board without native
code) the plain Python path runs. Both give the same bytes. ``codec.ENGINE`` says which is in use.

Measured on a Fruit Jam (RP2350, 150 MHz), per page of real sensor readings:

=========  ===========  ===========
Engine     pack         unpack
=========  ===========  ===========
Python     17.1 ms      2.5 ms
viper      0.60 ms      0.39 ms
=========  ===========  ===========

======================  =============================================
``codec_kernel`` file   Boards
======================  =============================================
``armv6m``              RP2040
``armv7emsp``           SAMD51 (M4), nRF52840, RP2350, STM32F4
``xtensawin``           ESP32-S2, ESP32-S3
``rv32imc``             ESP32-C3, C5, C6
======================  =============================================

Only the RP2350 has been tested. ``src/jampacked_turbo.py`` is the kernel source and
``src/Makefile`` builds it with ``mpy-cross -march`` for each architecture:

.. code-block:: shell

    cd src
    make MPY_CROSS=path/to/circuitpython/mpy-cross/build/mpy-cross

Use an ``mpy-cross`` from the CircuitPython version the files will run on.
``tests/test_kernel_model.py`` runs the kernel source under CPython against the plain Python path.

Documentation
=============
API documentation for this library can be found on `Read the Docs <https://docs.circuitpython.org/projects/jampacked/en/latest/>`_.

For information on building library documentation, please check out
`this guide <https://learn.adafruit.com/creating-and-sharing-a-circuitpython-library/sharing-our-docs-on-readthedocs#sphinx-5-1>`_.

Contributing
============

Contributions are welcome! Please read our `Code of Conduct
<https://github.com/adafruit/Adafruit_CircuitPython_JamPacked/blob/HEAD/CODE_OF_CONDUCT.md>`_
before contributing to help this project stay welcoming.
