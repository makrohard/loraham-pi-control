// fixture: LoRaHAM_Daemon loraham_daemon/hardware_profile.cpp at 660be550e3af762120f41a02d48b53af45ad109c (v1.3.1), verbatim below this line; refreshed with every daemon pin move
#include "hardware_profile.h"

#include <string.h>
#include <stdio.h>

/* --- Hardware profiles ---------------------------------------------------- */

DaemonHardwareProfile daemon_hw_profile;

static const char *g_hw_preset = "loraham";

bool daemon_set_hardware_preset(const char *name)
{
    if (!name || name[0] == '\0')
        return false;

    g_hw_preset = name;
    return true;
}

const char *daemon_hardware_preset_name(void)
{
    return g_hw_preset;
}

const char *daemon_chip_family_name(DaemonChipFamily family)
{
    switch (family) {
        case DAEMON_CHIP_FAMILY_SX127X:
            return "SX127x";
        case DAEMON_CHIP_FAMILY_SX1262:
            return "SX1262";
    }

    return "unknown";
}

const char *daemon_hardware_profile_known(void)
{
    return "loraham, uputronics-ce0, uputronics-ce1, waveshare-sx1262, meshstick-1262";
}

static void profile_claimed_finish(DaemonHardwareProfile *p)
{
    /* With kernel chip selects the CS line is the kernel's, never ours. */
    const int candidates[] = { p->kernel_cs ? DAEMON_HW_PIN_NC : p->cs,
                               p->irq, p->rst, p->gpio,
                               p->txen, p->rxen, p->led_pin };
    p->claimed_count = 0;

    for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
        if (candidates[i] < 0)
            continue;
        if (p->claimed_count < DAEMON_HW_MAX_CLAIMED)
            p->claimed[p->claimed_count++] = candidates[i];
    }
}

/*
 * Preset table. Sources: the pre-profile hardcoded wiring (loraham), pinout.xyz +
 * vendor statements (Uputronics), Waveshare wiki (SX1262 HAT). See
 * docs/hardware.md for the full derivation and the combination matrix.
 */
static bool profile_fill(const char *preset, int band, DaemonHardwareProfile *p)
{
    memset(p, 0, sizeof(*p));

    if (strcmp(preset, "loraham") == 0) {
        /* LoRaHAM_Pi dual-module wiring, bit-identical to the pre-profile
         * hardcoded pins (433: Module(hal,8,25,5,24); 868: (hal,7,16,6,12)). */
        p->name = "loraham";
        p->family = DAEMON_CHIP_FAMILY_SX127X;
        if (band == 433) {
            p->cs = 8;  p->irq = 25; p->rst = 5;  p->gpio = 24;
            p->led_pin = 13;
        } else {
            p->cs = 7;  p->irq = 16; p->rst = 6;  p->gpio = 12;
            p->led_pin = 19;
        }
        p->aux = DAEMON_HW_PIN_NC;
        p->txen = DAEMON_HW_PIN_NC;
        p->rxen = DAEMON_HW_PIN_NC;
        p->tcxo_voltage = 0.0f;
        p->cad_scan_available = true;
        p->fsk_stream_available = true;
        p->reset_wired = true;
        profile_claimed_finish(p);
        return true;
    }

    if (strcmp(preset, "uputronics-ce0") == 0 ||
        strcmp(preset, "uputronics-ce1") == 0) {
        /* Uputronics Pi Zero LoRa expansion board. DIO1 and RESET are NOT
         * routed: gpio/rst = NC. DIO5 is routed but unused (doc only).
         * LED lines BCM 6/13 are shared across stacked boards; assignment is
         * slot-based so two processes never claim the same line. */
        bool ce0 = (preset[strlen(preset) - 1] == '0');

        p->name = ce0 ? "uputronics-ce0" : "uputronics-ce1";
        p->family = DAEMON_CHIP_FAMILY_SX127X;
        if (ce0) {
            p->cs = 8;  p->irq = 25; p->aux = 24; p->led_pin = 6;
        } else {
            p->cs = 7;  p->irq = 16; p->aux = 12; p->led_pin = 13;
        }
        p->rst = DAEMON_HW_PIN_NC;      /* not connected on this board */
        p->gpio = DAEMON_HW_PIN_NC;     /* DIO1 not routed — never fake it */
        p->txen = DAEMON_HW_PIN_NC;
        p->rxen = DAEMON_HW_PIN_NC;
        p->tcxo_voltage = 0.0f;
        /* DIO1 is still not routed, but active CAD no longer needs it: the
         * SX127x driver reads the latched CadDone/CadDetected bits out of
         * RegIrqFlags. Before that repair the blocking scanChannel() could
         * never see CadDetected on this board and reported FREE for every
         * scan, so the flag had to be false and LBT fell back to the RSSI
         * threshold. */
        p->cad_scan_available = true;
        p->fsk_stream_available = false;
        p->reset_wired = false;
        profile_claimed_finish(p);
        (void)band;                     /* board works in either band */
        return true;
    }

    if (strcmp(preset, "waveshare-sx1262") == 0) {
        /* Waveshare SX1262 LoRaWAN Node HAT (SPI); LF and HF variants are
         * pin-identical, the band comes from --radio. RXEN switching is
         * internal via SX1262 DIO2; TCXO is powered from DIO3. */
        p->name = "waveshare-sx1262";
        p->family = DAEMON_CHIP_FAMILY_SX1262;
        p->cs = 21;
        p->irq = 16;                    /* DIO1 */
        p->rst = 18;
        p->gpio = 20;                   /* BUSY */
        p->aux = DAEMON_HW_PIN_NC;
        p->txen = 6;
        p->rxen = DAEMON_HW_PIN_NC;     /* via DIO2-as-RF-switch */
        p->tcxo_voltage = 1.8f;         /* TCXO fed from DIO3 */
        p->led_pin = DAEMON_HW_PIN_NC;  /* BCM 6 is TXEN here, no board LED */
        p->cad_scan_available = true;   /* SX126x hardware CAD */
        p->fsk_stream_available = false;
        p->reset_wired = true;
        profile_claimed_finish(p);
        (void)band;
        return true;
    }

    if (strcmp(preset, "meshstick-1262") == 0) {
        /* ELECROW Meshstick: SX1262 + TCXO behind a CH341 USB-to-SPI bridge,
         * EU868 hardware. Wiring and settings from meshtasticd's
         * bin/config.d/lora-usb-meshstick-1262.yaml (CS 0, IRQ 6, Reset 2,
         * Busy 4, RXen 1, DIO2_AS_RF_SWITCH, DIO3_TCXO_VOLTAGE true = 1.8 V,
         * USB 1A86:5512). The pins are CH341 D-lines, not BCM. */
        if (band != 868)
            return false;
        p->name = "meshstick-1262";
        p->family = DAEMON_CHIP_FAMILY_SX1262;
        p->cs = 0;                      /* D0 */
        p->irq = 6;                     /* D6 = DIO1 */
        p->rst = 2;                     /* D2 */
        p->gpio = 4;                    /* D4 = BUSY */
        p->aux = DAEMON_HW_PIN_NC;
        p->txen = DAEMON_HW_PIN_NC;
        p->rxen = 1;                    /* D1, besides the DIO2 RF switch */
        p->tcxo_voltage = 1.8f;         /* TCXO fed from DIO3 */
        p->led_pin = DAEMON_HW_PIN_NC;  /* no Pi line, no LED */
        p->cad_scan_available = true;   /* SX126x hardware CAD */
        p->fsk_stream_available = false;
        p->reset_wired = true;
        p->usb_ch341 = true;
        p->usb_vid = 0x1A86;
        p->usb_pid = 0x5512;
        p->power_max_dbm = 22;          /* ELECROW: "+22 dBm@Max" */
        p->claimed_count = 0;           /* nothing on the Pi's GPIO */
        return true;
    }

    return false;
}

bool daemon_hardware_profile_resolve(int band)
{
    return profile_fill(g_hw_preset, band, &daemon_hw_profile);
}

bool daemon_hardware_profile_apply_spi(DaemonHardwareProfile *p, bool kernel_cs)
{
    p->spi_channel = 0;
    p->kernel_cs = false;
    if (!kernel_cs)
        return true;
    if (p->cs != 8 && p->cs != 7)
        return false;
    p->spi_channel = (p->cs == 8) ? 0 : 1;
    p->kernel_cs = true;
    profile_claimed_finish(p);      /* drops CS from the claimed lines */
    return true;
}
