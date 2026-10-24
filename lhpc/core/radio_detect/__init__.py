"""Detect a radio's chip without the daemon: RadioLib's chip search (`chips`) over the Pi's SPI bus (`spi`)
or a CH341 USB bridge (`ch341`). Unused until Detect goes live (stage L9).

Each backend gives `chips` the same small bus: a RESET line, a BUSY line, one chip-selected transfer, and
a clock to sleep on. A refusal (a line or a stick another process holds, no matching stick) is a
`DetectRefused` with the reason; it touches no other line and writes to no device.
"""


class DetectRefused(Exception):
    """Detect did not run on this radio; the message says why (shown red, nothing else touched)."""
