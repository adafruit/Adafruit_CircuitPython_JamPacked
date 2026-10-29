# SPDX-FileCopyrightText: Copyright (c) 2026 Mikey Sklar for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""Keep readings while Wi-Fi is down, then send them in one batch.

Needs settings.toml with CIRCUITPY_WIFI_SSID, CIRCUITPY_WIFI_PASSWORD,
ADAFRUIT_AIO_USERNAME and ADAFRUIT_AIO_KEY, and a feed named "temperature".
"""

import os
import time

import adafruit_connection_manager
import adafruit_requests
import microcontroller
import wifi
from adafruit_io.adafruit_io import IO_HTTP

from adafruit_jampacked.samplelog import SampleLog

INTERVAL = 10  # seconds between readings
BATCH = 50  # readings per request

# Ring mode: if the network is gone for a long time, keep the newest readings.
log = SampleLog(capacity=4096, scale=100, signed=True, ring=True)
io = None


def connect():
    wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"), os.getenv("CIRCUITPY_WIFI_PASSWORD"))
    pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
    ssl = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
    requests = adafruit_requests.Session(pool, ssl)
    return IO_HTTP(os.getenv("ADAFRUIT_AIO_USERNAME"), os.getenv("ADAFRUIT_AIO_KEY"), requests)


def send_all():
    batch = []
    for value in log:
        batch.append({"value": value})
        if len(batch) == BATCH:
            io.send_batch_data("temperature", batch)
            batch = []
    if batch:
        io.send_batch_data("temperature", batch)
    log.clear()


while True:
    log.append(microcontroller.cpu.temperature)
    try:
        if io is None:
            io = connect()
        send_all()
        print("sent")
    except (OSError, RuntimeError, ConnectionError) as error:
        io = None
        print("offline, holding", len(log), "readings:", error)
    time.sleep(INTERVAL)
