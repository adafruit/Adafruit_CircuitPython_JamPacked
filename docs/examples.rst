Simple test
------------

Logs the CPU temperature, reads it back and prints the packing ratio.

.. literalinclude:: ../examples/jampacked_simpletest.py
    :caption: examples/jampacked_simpletest.py
    :linenos:

Hold readings offline for Adafruit IO
-------------------------------------

Keeps readings while Wi-Fi is down and sends them with ``send_batch_data`` when it is back.

.. literalinclude:: ../examples/jampacked_adafruitio.py
    :caption: examples/jampacked_adafruitio.py
    :linenos:

History across deep sleep
-------------------------

Saves the log to ``alarm.sleep_memory`` before each deep sleep. ESP32 boards only.

.. literalinclude:: ../examples/jampacked_sleep_memory.py
    :caption: examples/jampacked_sleep_memory.py
    :linenos:

Long history on a Sparkline
---------------------------

Keeps a packed history and draws the newest 100 points.

.. literalinclude:: ../examples/jampacked_sparkline.py
    :caption: examples/jampacked_sparkline.py
    :linenos:
