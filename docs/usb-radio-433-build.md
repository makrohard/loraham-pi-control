# Building a 433 MHz USB radio: CH341A + RFM98PW

A self-built USB radio: a CH341A USB-to-SPI board, an AT24C02N EEPROM that gives the stick its own
serial, and a HopeRF RFM98PW (SX127x, 433 MHz, high power). It reports the USB id `1a86:5512`, like
the other [USB radios](usb-radios.md). In LHPC it is the model `ch341-rfm98pw-433`, which needs
LoRaHAM daemon 1.4.0 (see the [changelog](../CHANGELOG.md) for the release that ships it).

The build order is: adapter checks → EEPROM wiring and programming → radio wiring → receive → controlled
transmit. Keep power off while soldering.

## Contents

- [Parts and tools](#parts-and-tools)
- [1. Prepare the CH341 board](#1-prepare-the-ch341-board)
- [2. The seven signal wires](#2-the-seven-signal-wires)
- [3. Radio pins and power](#3-radio-pins-and-power)
- [4. The EEPROM](#4-the-eeprom)
- [5. Program the EEPROM](#5-program-the-eeprom)
- [6. Boot from the EEPROM](#6-boot-from-the-eeprom)
- [7. Wire and power the radio](#7-wire-and-power-the-radio)
- [8. First receive, then transmit](#8-first-receive-then-transmit)
- [Sources](#sources)

## Parts and tools

- A CH341A board (the build used eBay item 176153644271, MPN `UTTL_IIC_SPI`). The pad and jumper names
  below are that board's; check yours by its silk labels and by continuity.
- An AT24C02N EEPROM (256 bytes, SOIC-8, 2.7–5.5 V; run it at 3.3 V), with 100 nF across its pins 8 and 4.
- A HopeRF RFM98PW module on a carrier with a ground plane, a 433 MHz antenna or a 50 Ω dummy load.
- A multimeter, fine soldering tools and short insulated wires.

## 1. Prepare the CH341 board

With USB unplugged and nothing attached:

1. Set **both** voltage jumper caps (at the USB end) to **3V3**. All signal lines in this build are
   3.3 V logic; the radio's digital inputs must stay below 3.6 V.
2. Set the mode jumper to **I2C-SPI** for the first checks and for programming a blank EEPROM.
3. Plug in USB with only this board attached and check the id: `lsusb -d 1a86:5512` must show one
   line. `1a86:5523` means UART mode: unplug and check the mode jumper.

Measure against the IC, not against a header numbering: CH341A pin 12 is GND, pins 28 and 9 are on the
3.3 V supply (each about 3.3 V with USB on; near 5 V means the voltage caps are wrong — unplug).

## 2. The seven signal wires

The tags W1–W7 are this guide's labels, not board markings. Use the board's underside D-line pads; the
IC pin numbers are for checking continuity.

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
- After soldering, check every wire end to end and each neighbour for a solder bridge, with power off.

## 3. Radio pins and power

RFM98PW module pins (top view, shield side, as in HopeRF's drawing; use the module's pin numbers, not
the chip's):

| pin | use | pin | use |
|---|---|---|---|
| 1 | ANT (RF feed) | 9 | 3.3 V output — **leave open** |
| 2 | GND | 10 | MISO (W4) |
| 3 | DIO3 (unused) | 11 | MOSI (W3) |
| 4 | DIO4 (unused) | 12 | SCK (W2) |
| 5 | **VCC, 5–6 V during TX** | 13 | NSS (W1) |
| 6 | DIO0 (W6) | 14 | RESET (W5) |
| 7 | DIO1 (W7) | 15 | DIO5 (unused) |
| 8 | DIO2 (unused) | 16 | GND |
| 17 | underside thermal pad: solder it to grounded copper | | |

**Power, as built:** in the reference build the module's VCC (pin 5) is fed **5 V directly** from the
USB supply. Never feed pin 5 from the board's 3.3 V, and never tie pin 9 to anything.

What that choice means:
- HopeRF specifies 650 mA typical and 700 mA maximum for the module alone at +30 dBm on 433 MHz, plus
  the bridge's own current.
- The stick's USB descriptor asks the host for 100 mA, and a USB 2.0 device gets no more than 500 mA
  from a standard port.
- So start at the **lowest** transmit power and watch the kernel log (`journalctl -k -f`) for a USB
  disconnect or an over-current message.
- A separately regulated 5–6 V supply, with the grounds joined and its positive never tied to USB VBUS,
  is the alternative for high power.

## 4. The EEPROM

The EEPROM holds the CH341's USB configuration, including an eight-character serial that tells this
stick apart from every other `1a86:5512` device. It holds no radio firmware.

| AT24C02N pin | connect to |
|---|---|
| 1 A0, 2 A1, 3 A2 | GND (I2C address 0x50) |
| 4 GND | GND |
| 5 SDA | the board's SDA header pin (CH341A IC pin 23) |
| 6 SCL | the board's SCL header pin (CH341A IC pin 24) |
| 7 WP | GND (write enabled) |
| 8 VCC | the board's 3V3 |

SDA and SCL are not D5 or D6. Add 4.7 kΩ pull-ups to 3.3 V on SDA and SCL only if the idle lines do
not sit near 3.3 V with USB on.

## 5. Program the EEPROM

Keep the radio disconnected, keep the mode jumper on **I2C-SPI**, and keep only this CH341 plugged in:
the tool opens the first `1a86:5512` it finds.

Build the tool at the commit the guide checked:

```bash
sudo apt install git build-essential libusb-1.0-0-dev python3 usbutils
git clone https://github.com/command-tab/ch341eeprom.git
cd ch341eeprom
git checkout --detach 7cffbef7552d93162bd90cae836a45e94acb93fb
make CC=gcc
```

**Back up twice before writing.** The tool can exit 0 on an error, so compare the files rather than
trust its exit status:

```bash
./ch341eeprom -v -s 24c02 -r backup-a.bin
./ch341eeprom -v -s 24c02 -r backup-b.bin
python3 -c "import pathlib as p; a=p.Path('backup-a.bin').read_bytes(); b=p.Path('backup-b.bin').read_bytes(); assert len(a)==len(b)==256 and a==b; print('PASS')"
```

If your user cannot open the device, run the tool with `sudo`. Keep a copy of `backup-a.bin` outside
this directory. An all-`FF` dump is a blank chip, or a failed read.

**Create the image** (256 bytes: signature and SPI mode, VID 1A86, PID 5512, release 0100, the serial at
0x10). Choose a different eight-character serial for every stick:

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

**Write once, then verify into a new file:**

```bash
./ch341eeprom -v -s 24c02 -w loraham-24c02.bin
./ch341eeprom -v -s 24c02 -r readback-01.bin
python3 -c "import pathlib as p; assert p.Path('loraham-24c02.bin').read_bytes()==p.Path('readback-01.bin').read_bytes(); print('PASS')"
```

Do not unplug during the write. If it fails, check 3.3 V on EEPROM pin 8, WP at 0 V, idle SDA/SCL, the
address pins and the chip's orientation. Name each new readback file differently (02, 03 …).

## 6. Boot from the EEPROM

Measured on the reference board:

| mode jumper | the CH341 at power-up |
|---|---|
| on **I2C-SPI** | ignores the EEPROM: bcdDevice 3.04, **no serial** |
| **removed** | loads the EEPROM: bcdDevice 1.00 and your serial |

So program a blank EEPROM with the jumper on I2C-SPI, then **remove the jumper** for normal use.
Rewrites also work with the jumper removed (the stick stays `1a86:5512`). Programming a *blank*
EEPROM with the jumper removed was not tested.

Unplug, remove the jumper, plug in, and check that the serial is there:

```bash
lsusb -d 1a86:5512 -v | grep -i iserial
```

Pass: your serial survives a second unplug and replug. With `5512` and no serial, the EEPROM was not
loaded: check the jumper and the SDA/SCL wiring. To restore the old contents, write `backup-a.bin` the
same way and verify it.

## 7. Wire and power the radio

With all power off, solder the seven signals and the grounds (section 2) and the 5 V feed (section 3).
Check every wire end to end, and radio pins 2, 16 and the thermal pad to the board GND.

LHPC's one-time USB setup gives your user access to the stick (the console's Hardware page shows it
when the host needs it). The build needs no driver blacklist.

At power-up, radio pin 5 must read 5.0–6.0 V and the board 3V3 about 3.3 V. Stop at a reversed or
out-of-range supply.

The first SPI check reads the chip's version register (0x42). An RFM98PW reads `0x12`.

## 8. First receive, then transmit

1. **Receive first.** Match a known-good 433 MHz peer in frequency, bandwidth, spreading factor, coding
   rate, sync word, header/CRC and IQ, and confirm that its frames arrive. The reference build received
   MeshCom frames at 433.175 MHz, SF11, BW 250 kHz, CR 4/6, sync 0x2B, preamble 16.
2. **Then transmit, at the lowest power**, into an antenna or a dummy load rated above the expected
   power. Watch the kernel log for a USB disconnect, and check the module's temperature. The reference
   build sent one MeshCom frame at the lowest drive, without a USB event. Higher power was not tested
   with the 5 V USB feed.
3. Before you trust the build, check that TX returns to RX, that a daemon restart works, and that the
   stick comes back after an unplug.

## Sources

The full source list is in the build guide (v2, 23 Oct 2026), from which this page is translated:
- HopeRF RFM98PW/RFM95PW REV1.1 (pins, limits, current);
- WCH CH341DS1 v2D (pins, 3.3 V operation, EEPROM format);
- Atmel AT24C02 doc0180;
- command-tab/ch341eeprom at 7cffbef7;
- the USB-IF power policy;
- markbirss/MESHSTICK (the Meshstick reference wiring).

Section 6's jumper table and the receive/transmit results in section 8 were measured on the reference build.
