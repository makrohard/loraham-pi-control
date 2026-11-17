# Building a 433 MHz USB radio: CH341A + RFM98PW

A self-built USB radio: a CH341A USB-to-SPI board, an AT24C02N EEPROM that gives the stick its own
serial, and a HopeRF RFM98PW (SX127x, 433 MHz, high power). It reports the USB id `1a86:5512`, like
the other [USB radios](usb-radios.md). In LHPC it is the model `ch341-rfm98pw-433`, which needs
LoRaHAM daemon 1.4.0 (see the [changelog](../CHANGELOG.md) for the release that ships it).

Keep power off while soldering. The EEPROM is programmed before the radio is wired (steps 3–5).

## Contents

- [1. Parts and tools](#1-parts-and-tools)
- [2. Prepare the CH341 board](#2-prepare-the-ch341-board)
- [3. Solder the EEPROM](#3-solder-the-eeprom)
- [4. Program the EEPROM](#4-program-the-eeprom)
- [5. Boot from the EEPROM](#5-boot-from-the-eeprom)
- [6. Wire the radio](#6-wire-the-radio)
- [7. Power and capacitors](#7-power-and-capacitors)
- [8. First test at low power](#8-first-test-at-low-power)
- [Sources](#sources)

## 1. Parts and tools

- A CH341A board (the build used eBay item 176153644271, MPN `UTTL_IIC_SPI`). The pad and jumper names
  below are that board's; check yours by its silk labels and by continuity.
- An AT24C02N EEPROM (256 bytes, SOIC-8, 2.7–5.5 V; run it at 3.3 V) and a 100 nF ceramic capacitor.
- A HopeRF RFM98PW module on a carrier with a ground plane, a 433 MHz antenna or a 50 Ω dummy load.
- A 470 µF electrolytic capacitor (10 V or more; 16 V used) and a second 100 nF ceramic.
- A multimeter, fine soldering tools and short insulated wires.

## 2. Prepare the CH341 board

![CH341A board underside with the pads this build uses, and the CH341A pinout](img/usb-radio-433-ch341a.svg)

With USB unplugged and nothing attached:

1. Set **both** voltage jumper caps (at the USB end) to **3V3**. All signal lines in this build are
   3.3 V logic; the radio's digital inputs must stay below 3.6 V.
2. Set the mode jumper to **I2C-SPI** for the first checks and for programming a blank EEPROM.
3. Plug in USB with only this board attached and check the id: `lsusb -d 1a86:5512` must show one
   line. `1a86:5523` means UART mode: unplug and check the mode jumper.
4. Measure against the IC, not against a header numbering: CH341A pin 12 is GND, pins 28 and 9 are on
   the 3.3 V supply (each about 3.3 V with USB on; near 5 V means the voltage caps are wrong — unplug).

## 3. Solder the EEPROM

The EEPROM holds the CH341's USB configuration, including an eight-character serial that tells this
stick apart from every other `1a86:5512` device. It holds no radio firmware. Its wiring is the lower
part of the drawing in [step 6](#6-wire-the-radio).

| AT24C02N pin | connect to |
|---|---|
| 1 A0, 2 A1, 3 A2 | GND (I2C address 0x50) |
| 4 GND | GND |
| 5 SDA | the board's SDA header pin (CH341A IC pin 23) |
| 6 SCL | the board's SCL header pin (CH341A IC pin 24) |
| 7 WP | GND (write enabled) |
| 8 VCC | the board's 3V3, with 100 nF from pin 8 to pin 4 |

SDA and SCL are not D5 or D6. Add 4.7 kΩ pull-ups to 3.3 V on SDA and SCL only if the idle lines do
not sit near 3.3 V with USB on.

## 4. Program the EEPROM

Keep the radio disconnected, keep the mode jumper on **I2C-SPI**, and keep only this CH341 plugged in:
the tool opens the first `1a86:5512` it finds.

1. Build the tool at the commit the guide checked:

   ```bash
   sudo apt install git build-essential libusb-1.0-0-dev python3 usbutils
   git clone https://github.com/command-tab/ch341eeprom.git
   cd ch341eeprom
   git checkout --detach 7cffbef7552d93162bd90cae836a45e94acb93fb
   make CC=gcc
   ```

2. **Back up twice before writing.** The tool can exit 0 on an error, so compare the files rather than
   trust its exit status:

   ```bash
   ./ch341eeprom -v -s 24c02 -r backup-a.bin
   ./ch341eeprom -v -s 24c02 -r backup-b.bin
   python3 -c "import pathlib as p; a=p.Path('backup-a.bin').read_bytes(); b=p.Path('backup-b.bin').read_bytes(); assert len(a)==len(b)==256 and a==b; print('PASS')"
   ```

   If your user cannot open the device, run the tool with `sudo`. Keep a copy of `backup-a.bin` outside
   this directory. An all-`FF` dump is a blank chip, or a failed read.

3. **Create the image** (256 bytes: signature and SPI mode, VID 1A86, PID 5512, release 0100, the serial
   at 0x10). Choose a different eight-character serial for every stick:

   ```bash
   python3 - <<'PY'
   from pathlib import Path
   serial = b'LH433001'                       # exactly 8 printable ASCII characters
   assert len(serial) == 8 and all(0x21 <= c <= 0x7e for c in serial)
   image = bytearray(256)
   image[0:10] = bytes.fromhex('53 12 DC 00 86 1A 12 55 00 01')
   image[0x10:0x18] = serial
   Path('loraham-24c02.bin').write_bytes(image)
   for o in (0x00, 0x10):
       print(f'{o:02X}:', image[o:o+16].hex(' '))
   PY
   ```

   For the example serial the two rows read
   `00: 53 12 dc 00 86 1a 12 55 00 01 00 …` and `10: 4c 48 34 33 33 30 30 31 00 …`.

4. **Write once, then verify into a new file:**

   ```bash
   ./ch341eeprom -v -s 24c02 -w loraham-24c02.bin
   ./ch341eeprom -v -s 24c02 -r readback-01.bin
   python3 -c "import pathlib as p; assert p.Path('loraham-24c02.bin').read_bytes()==p.Path('readback-01.bin').read_bytes(); print('PASS')"
   ```

   Do not unplug during the write. If it fails, check 3.3 V on EEPROM pin 8, WP at 0 V, idle SDA/SCL,
   the address pins and the chip's orientation. Name each new readback file differently (02, 03 …).

## 5. Boot from the EEPROM

Measured on the reference board:

| mode jumper | the CH341 at power-up |
|---|---|
| on **I2C-SPI** | ignores the EEPROM: bcdDevice 3.04, **no serial** |
| **removed** | loads the EEPROM: bcdDevice 1.00 and your serial |

1. Unplug, **remove the mode jumper** (normal use), plug in, and check that the serial is there:
   `lsusb -d 1a86:5512 -v | grep -i iserial`.
2. Pass: your serial survives a second unplug and replug. With `5512` and no serial, the EEPROM was not
   loaded: check the jumper and the SDA/SCL wiring.

Rewrites also work with the jumper removed (the stick stays `1a86:5512`). Programming a *blank* EEPROM
with the jumper removed was not tested. To restore the old contents, write `backup-a.bin` as in step 4
and verify it.

## 6. Wire the radio

![RFM98PW module pinout, top view](img/usb-radio-433-rfm98pw.svg)

![Wiring of the CH341A board, the RFM98PW, the capacitors and the EEPROM](img/usb-radio-433-wiring.svg)

With USB unplugged, solder the seven signal wires and the grounds. The tags W1–W7 are this guide's
labels, not board markings. Use the board's underside D-line pads; the IC pin numbers are for checking
continuity. RFM98PW pin numbers are the module's (top view, shield side, as in HopeRF's drawing), not
the chip's.

| wire | CH341 pad | CH341A IC pin | RFM98PW pin |
|---|---|---|---|
| W1 | D0 / CS0 | 15 | 13 NSS |
| W2 | D3 / SCK | 18 | 12 SCK |
| W3 | D5 / MOSI | 20 | 11 MOSI |
| W4 | D7 / MISO | 22 | 10 MISO |
| W5 | D2 / CS2 | 17 | 14 RESET |
| W6 | D6 | 21 | 6 DIO0 (the interrupt) |
| W7 | D4 | 19 | 7 DIO1 |
| — | GND | 12 | 2, 16 and the underside pad 17 |

- RESET is **D2 / CS2**. Do not use the board's separate pad labelled RST.
- Compared with the Meshstick (an SX1262), D6 carries DIO0 instead of DIO1, and D4 carries DIO1 instead of
  BUSY: the SX127x has no BUSY line.
- Solder the underside pad 17 to grounded copper. Pin 1 (ANT) goes to the antenna or the dummy load.
  Pins 3, 4, 8 and 15 (DIO3, DIO4, DIO2, DIO5) stay open.
- After soldering, check every wire end to end, radio pins 2, 16 and pad 17 to the board GND, and each
  neighbour for a solder bridge, with power off.

## 7. Power and capacitors

1. Feed the module's VCC (pin 5) with **5 V directly** from the USB supply, as in the reference build.
   Never feed pin 5 from the board's 3.3 V. Pin 9 is the module's 3.3 V **output**: never tie it to
   anything.
2. Across VCC (pin 5) and GND, as close to the module as possible: the 470 µF electrolytic (its stripe,
   the minus side, to GND) and the 100 nF ceramic. Without them the PA's current pulse at each
   transmission drops the CH341 off USB.
3. At power-up, radio pin 5 must read 5.0–6.0 V and the board 3V3 about 3.3 V. Stop at a reversed or
   out-of-range supply.

What the USB feed means:
- HopeRF specifies 650 mA typical and 700 mA maximum for the module alone at +30 dBm on 433 MHz, plus
  the bridge's own current.
- The stick's USB descriptor asks the host for 100 mA, and a USB 2.0 device gets no more than 500 mA
  from a standard port.
- So start at the **lowest** transmit power and watch the kernel log (`journalctl -k -f`) for a USB
  disconnect or an over-current message.
- Measured with those capacitors on a USB-powered port: up to `POWER` 14 the stick stays; from 15 it drops off
  USB (brown-out). LHPC starts it at 14.
- +20 dBm (`lhpc hardware --high-power <rid> on`, `POWER=20`) on this stick is **unmeasured**. LHPC admits it,
  because the model has the high-power switch, but it has not been tried on USB power. Leave it off until it is
  measured with an antenna or a dummy load.
- A separately regulated 5–6 V supply, with the grounds joined and its positive never tied to USB VBUS,
  is the alternative for high power.

## 8. First test at low power

LHPC's one-time USB setup gives your user access to the stick (the console's Hardware page shows it
when the host needs it). The build needs no driver blacklist.

1. **SPI check.** The first SPI check reads the chip's version register (0x42). An RFM98PW reads `0x12`.
2. **Receive first.** Match a known-good 433 MHz peer in frequency, bandwidth, spreading factor, coding
   rate, sync word, header/CRC and IQ, and confirm that its frames arrive. The reference build received
   MeshCom frames at 433.175 MHz, SF11, BW 250 kHz, CR 4/6, sync 0x2B, preamble 16.
3. **Then transmit, at the lowest power**, into an antenna or a dummy load rated above the expected
   power. Watch the kernel log for a USB disconnect, and check the module's temperature. The reference
   build sent one MeshCom frame at the lowest drive, without a USB event.
4. Before you trust the build, check that TX returns to RX, that a daemon restart works, and that the
   stick comes back after an unplug.

## Sources

This page is translated from the build guide (v2, 23 Oct 2026). Its drawings are new, drawn from the
datasheets' pin tables:
- HopeRF, RFM98PW/RFM95PW datasheet, REV1.1 (module pins, supply, current);
- WCH, CH341 datasheet CH341DS1, version 2D (pins, 3.3 V operation, EEPROM format);
- Atmel, AT24C01A/02/04/08A/16A datasheet, doc0180 (EEPROM pins);
- command-tab/ch341eeprom at 7cffbef7;
- the USB-IF power policy;
- markbirss/MESHSTICK (the Meshstick reference wiring).

The jumper table in step 5, the `POWER` limit in step 7 and the receive and transmit results in step 8 were
measured on the reference build.
