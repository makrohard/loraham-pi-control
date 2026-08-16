"""Test-lab fake `spidev` — an SX127x-shaped register file behind the exact spidev API
surface loraham-rns-interface uses (open/close, max_speed_hz, mode, no_cs readback,
xfer2). Injected ONLY into the lab's reticulum node process via PYTHONPATH (the venv
build still imports the real system bindings). Register model: RegVersion 0x42 reads
0x12 (the one mandatory init read); RegIrqFlags 0x12 is write-1-to-clear and gains
TX_DONE (0x08) when RegOpMode enters TX, so transmits confirm instead of timing out;
everything else stores writes and reads back 0."""


class SpiDev:
    def __init__(self):
        self.max_speed_hz = 0
        self.mode = 0
        # Read back truthy after assignment — the no-CS handshake is the gate that
        # otherwise falls through to a /sys chip-select stat that no container has.
        self.no_cs = False
        self._r = [0] * 0x80
        self._r[0x42] = 0x12                       # SX127x RegVersion

    def open(self, bus, dev):
        pass

    def close(self):
        pass

    def _write(self, reg, val):
        if reg == 0x12:                            # IRQ flags: write-1-to-clear
            self._r[0x12] &= ~val & 0xFF
            return
        if reg == 0x01 and (val & 0x07) == 0x03:   # OpMode -> TX: confirm immediately
            self._r[0x12] |= 0x08                  # IRQ_TX_DONE
        if reg != 0x42:
            self._r[reg] = val & 0xFF

    def xfer2(self, data):
        addr = data[0]
        reg = addr & 0x7F
        if addr & 0x80:                            # write burst (FIFO never autoincs)
            for i, val in enumerate(data[1:]):
                self._write(reg if reg == 0x00 else reg + i, val)
            return [0] * len(data)
        return [0] + [self._r[reg] if reg == 0x00 else self._r[(reg + i) & 0x7F]
                      for i in range(len(data) - 1)]
