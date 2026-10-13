# Honest Millisecond Clock

This is a clock that displays the time to the millisecond, and is honest
about it. The millisecond digits change on the exact millisecond boundary of
a GPS-disciplined 10 MHz timebase, so a photograph of the display is a valid
timestamp.

The NoScheme sources in this directory implement this design; see
[README.md](README.md) for building, sourcing, and assembly notes.

## Hardware Features

### Display

This clock has two displays. A 2.42" SSD1309 OLED module on SPI shows the
date and ancillary data: time source, lock state, estimated accuracy, and
network status.

The second display is nine 0.56" common-anode 7-segment digits (Lite-On
LSHD-5601, green) showing HH:MM:SS.mmm. The digits are not multiplexed: each
digit has its own TI TLC5916 8-bit constant-current sink driver, and a tenth
TLC5916 drives the four colon dots and four front-panel annunciators (GPS lock,
NTP, holdover, PD fault). The ten drivers form one 80-bit shift chain:

- Immediately _after_ each millisecond boundary, firmware shifts the next
  millisecond's 80 bits in over SPI4 (8 µs at 10 MHz).
- A timer output-compare pulses the shared LE (latch) line at the exact next
  boundary, so every digit changes at once.
- The chain's last SDO returns to the MCU, so firmware can verify the chain.
- OE# is a timer PWM output for brightness control, pulled high (blank) until
  firmware has loaded a valid time.

A 1.8 kΩ R-EXT sets about 10.4 mA per segment. The drivers' logic runs at
3.3 V (so the MCU meets VIH); the LED anodes run from 5 V.

An ambient-light phototransistor (Everlight ALS-PT19) feeds an ADC input so
the PWM brightness can follow the room.

### Time Source

The clock gets its time from GPS, or from NTP when GPS is unavailable. With
GPS, the PPS edge both sets the time and disciplines the oscillator toward
exactly 10,000,000 cycles per PPS interval, averaged over time. The clock then
serves NTP as a stratum 1 server: GPS itself is the stratum 0 reference.

Without GPS, it synchronizes to upstream NTP servers and the display reports
the reduced accuracy.

### Timing Path

All timing lives in TIM2, a 32-bit timer:

| Signal | Pin | Function |
|---|---|---|
| 10 MHz (timer mode) | PA15 | TIM2_ETR: TIM2 counts oscillator cycles directly |
| GPS PPS | PB10 | TIM2_CH3 input capture: cycles per second, for discipline |
| Display latch | PA3 | TIM2_CH4 output compare: one pulse every 10,000 cycles |

A three-pin clock jumper (JP) chooses where the 10 MHz goes:

- **Shunt 2–3 (timer):** into TIM2_ETR. The latch is then derived directly
  from oscillator cycles. The MCU core runs from its internal oscillator, or
  from an optional crystal (the HSE crystal and its load caps are
  do-not-fit footprints).
- **Shunt 1–2 (HSE):** into OSC_IN as an HSE bypass clock. The whole MCU,
  including TIM2 and the Ethernet timestamp clock, then runs from the
  disciplined timebase.

### GPS Module

A u-blox MAX-M10S provides time and position over USART3 (NMEA or UBX) and a
PPS time pulse with tens of nanoseconds of jitter. VCC_RF biases an active
antenna on the GPS SMA through a 10 Ω resistor and a 27 nH choke; RF_IN is
DC-blocked inside the module. The MCU controls RESET_N and EXTINT.

### Oscillator

The board has footprints for two 10 MHz oscillators; fit one:

- **VCTCXO (fitted by default):** a 3.2 × 2.5 mm, 3.3 V part (ECS
  ECS-VC-TXO32) on the quiet rail. A 1 kΩ series resistor protects its control
  input; firmware limits the EFC code to the part's 0–3.3 V control range.
- **OCXO (alternative):** a 14-pin DIP-format can. Fit one 0 Ω link to power
  it from 12 V or from 5 V, and move the do-not-fit coupling capacitor from the
  VCTCXO to the OCXO. It draws several watts while warming up.

The trim (EFC) chain is a TI DAC8551 16-bit DAC with a REF3025 2.5 V
reference on the quiet 3.3 V rail. A 10 kΩ / 10 µF filter (1.6 Hz) follows it,
then an MCP6001 non-inverting stage with a gain of 1.56, powered from 5 V. That
gives 0–3.9 V of EFC, about 60 µV per DAC step: well under a part per billion
per step for typical VCTCXO pull slopes.

Each oscillator output is AC-coupled into a 74LVC1G17 Schmitt buffer biased at
mid-rail, so a CMOS, clipped-sine, or sine output all square up cleanly.

### MCU

The heart of the clock is an **STM32H723VGT6** (Cortex-M7, 550 MHz, LQFP-100).
The original choice, the STM32H533, has no Ethernet MAC. The STM32H563 and
H573, the only STM32H5 parts with one, are hard to get. The H723's Ethernet MAC
has IEEE 1588 hardware timestamps, which is what makes accurate NTP possible.
LCSC had about 11,000 in stock (C730142) on 2026-10-13.

It runs in the LDO supply configuration (the LQFP-100 H723 has no SMPS), with
2.2 µF on each VCAP pin.

### Network Interface

No STM32 has a gigabit MAC suited to this, and NTP needs no more than 10/100
anyway. The MAC connects over RMII to a TI DP83848C PHY (LQFP-48, so no QFN)
and a HanRun HR911105A RJ45 with integrated magnetics. The jack's green LED
shows link and its yellow LED shows activity. A dedicated 50 MHz oscillator
clocks both the PHY's X1 and the MAC's ETH_REF_CLK, so the network never
depends on the clock jumper.

### Connections

- **GPS antenna:** an edge-mount SMA with active-antenna bias.
- **10 MHz:** an edge-mount SMA. A three-pin direction jumper selects:
  - **Output (shunt 1–2, also the default with no shunt):** a 74LVC1G125
    drives the local 10 MHz through 33 Ω.
  - **Input (shunt 2–3):** the buffer goes high-impedance, and a 74LVC1G3157
    switch routes the squared SMA input to the clock jumper in place of the
    local oscillator.

  A 49.9 Ω termination footprint is provided for terminated input use.
- **SWD:** the standard 10-pin 0.05" Cortex debug header.
- **Probe header (2×8, 0.1"):** ground, every rail, PPS, EFC, the display
  latch, blank, data and clock, the GPS UART, PD power-good, and the 10 MHz
  direction.

### Power

One USB-C receptacle carries both power and the debug console.

- A WCH CH224K PD sink controller requests **12 V** using I/O-level
  configuration (CFG1 = 0, CFG2 = 0, CFG3 = 1). Its DP/DM fast-charge pins are
  left unconnected, so the USB data pair goes to the MCU's USB (OTG HS with the
  internal full-speed PHY) through a USBLC6-2SC6 ESD clamp.
- PG (open-drain, active low) reports a successful PD contract to the MCU.
- An SMAJ15A clamps the input.

| Rail | Source | Loads |
|---|---|---|
| VBUS, 12 V after PD | USB-C source | Both bucks, the 12 V OCXO option |
| 5 V | AP63205 buck (2 A) | LED anodes, EFC amplifier, quiet LDO, 5 V OCXO option |
| 3.3 V | AP63203 buck (2 A) | MCU, PHY, GPS, LED driver logic, OLED, logic |
| 3.3 V quiet | LP5907 LDO from 5 V | VCTCXO, DAC, reference, local squarer |

Without a PD source, VBUS stays at 5 V. The 3.3 V rail still runs, but the
5 V buck drops out, and the firmware should report the PD fault rather than
light the display.

Hand power budget (the NoScheme sources do not yet carry per-pin load data):

| Load | Rail | Worst case |
|---|---|---|
| 80 LED outputs at 10.4 mA | 5 V | 0.83 A (all segments lit) |
| MCU at 550 MHz with Ethernet | 3.3 V | about 0.15 A |
| DP83848C at 100 Mb/s | 3.3 V | about 0.08 A |
| MAX-M10S plus active antenna | 3.3 V | about 0.05 A |
| Ten TLC5916 (logic), OLED, oscillators, logic | 3.3 V | about 0.15 A |
| OCXO warm-up (option) | 12 V or 5 V | 2–3 W |

That is about 5 W on 5 V and 1.5 W on 3.3 V. With the OCXO warming up, the
total stays near 10 W, or 0.85 A at 12 V, within common 12 V / 1.5 A PD
profiles.

## Design Constraints

* Use SMT parts where possible. The exceptions are the digits, the RJ45, the
  SMAs, the headers, and the OCXO option.
* Use "jellybean" high-availability parts where possible: JLCPCB/LCSC
  assembly, with LCSC numbers recorded in each part's BOM metadata.
* Provide ample test points, aggregated into headers where convenient.
* Avoid QFNs. The PHY is an LQFP, and every other IC is an SOIC, SSOP, SOT, or
  LCC module.
* Use 0603 or larger passives.

## Open Items

* The VCTCXO is a DigiKey part that LCSC does not stock: consign it to JLC or
  fit it by hand.
* LCSC showed no TLC5916 stock on 2026-10-13 (JLCPCB still listed it). The
  pin-compatible STP08CP05 is the alternative.
* LCSC lists the DP83848C as not recommended for new designs. It is still the
  only common 10/100 PHY that avoids a QFN.
* WCH now recommends the pin-compatible CH224A for new designs. Switching to it
  would mean changing the VDD feed and configuration resistors.
* Confirm the final pin assignment and clock tree in STM32CubeMX.
