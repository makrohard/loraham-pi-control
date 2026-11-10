# USB radios

A USB radio is a LoRa module behind a CH341 USB-to-SPI bridge. The [daemon](stacks/daemon.md) drives
it over USB with the CH341 library (the project's fork of pine64's); no Pi GPIO or SPI line is used. Each stick is one radio of the
hardware list (`config/radios.toml`), added on the console's *Add Hardware* page and bound on a radio's detail page
(*Apps › Daemon › Hardware*, `/hardware`).

## Contents

- [The sticks](#the-sticks)
- [One USB id for every stick](#one-usb-id-for-every-stick)
- [Adding a stick](#adding-a-stick)
- [Host access](#host-access)

## The sticks

| stick | chip | band | model in LHPC | state in this release |
|---|---|---|---|---|
| ELECROW Meshstick | SX1262 + TCXO | 868 MHz | `meshstick-1262` | supported |
| [PINE64 USB LoRa adapter](https://pine64.org/documentation/Pinedio/USB_adapter/) (Pinedio) | SX1262 | 868 / 915 MHz (LHPC uses 868) | `pinedio-usb-1262` | supported |
| CH341A + RFM98PW, a self-built stick ([build guide](usb-radio-433-build.md)) | SX127x (RFM98PW, high power) | 433 MHz | `ch341-rfm98pw-433` | supported |

The release that ships these sticks is listed in the [changelog](../CHANGELOG.md).

**PINE64 adapter:** PINE64's page above is the reference for its wiring and drivers. Its IRQ and
BUSY lines sit on the CH341's INT# and SLCT status pins, not on the D0–D7 lines the other sticks use.

## One USB id for every stick

All three sticks report the same USB id, `1a86:5512` (the CH341 in SPI mode). LHPC therefore never
guesses a stick's model from the id or from its serial:

- a stick with a serial (the Meshstick; a self-built stick with a programmed EEPROM) is identified by
  that serial, on any port;
- a stick without one (the PINE64 adapter reports none) is identified by its USB port, so keep it on
  the same port;
- the serial only says *which* stick it is; the model is always your choice when you add it.

## Adding a stick

On *Add Hardware* (`/hardware/add`) each listed stick shows its serial (or, without one, its port). **Add**
creates a radio for the chosen model on that stick; **Re-bind** on a radio's detail page points that radio at a
stick. A start is refused by name when the radio's stick or the access to it is missing. Two radios on one band
each run their own daemon: address one by its radio id (`lhpc stack stop daemon@rN`).

## Host access

The daemon opens the stick as your user. The console shows the one-time USB setup as one command
where the host needs it; a start without access is refused naming that command.
