#pragma once

// =============================================================================
// XploraVentures — ESP32-S3 Pin Definitions
// Board: ESP32-S3-DevKitC-1
// =============================================================================

// -----------------------------------------------------------------------------
// I2C Bus  (shared: MAX17048 fuel gauge, PCF8575 expander)
// Stack_Top_16 = SCL, Stack_Top_17 = SDA via stacking connector
// -----------------------------------------------------------------------------
#define I2C_SDA     8
#define I2C_SCL     9

// -----------------------------------------------------------------------------
// SPI Bus  (shared: SD card, PT1000 boards, MCP2515 CAN)
// Stack_Top_4 = MOSI, Stack_Top_5 = CLK, Stack_Top_6 = MISO
// -----------------------------------------------------------------------------
#define SPI_MOSI    35
#define SPI_SCK     36
#define SPI_MISO    37

// -----------------------------------------------------------------------------
// SD Card
// -----------------------------------------------------------------------------
#define SD_DETECT   13              // Card detect, active-LOW (R24 10k pull-up to 3V3)
#define SD_CS       14

// -----------------------------------------------------------------------------
// Temperature Expansion Board — PT1000 RTD + SHT Temp/Humidity sensors
//
// PT1000 (SPI, shares bus above)
// Four CS lines routed via stacking connector (J15/J17 pins 11-14):
//   Pin 11 = Board1_PT1000_1_CS
//   Pin 12 = Board1_PT1000_2_CS
//   Pin 13 = Board2_PT1000_1_CS
//   Pin 14 = Board2_PT1000_2_CS
// -----------------------------------------------------------------------------
#define PT1000_B1_CH1_CS    7       // Stack_Top_11
#define PT1000_B1_CH2_CS    10      // Stack_Top_12
#define PT1000_B2_CH1_CS    11      // Stack_Top_13
#define PT1000_B2_CH2_CS    12      // Stack_Top_14

// SHT Temp/Humidity sensors — 8x via TCA9548A I2C mux (U2)
// A0=L, A1=L, A2=L (JP1/JP2/JP3 all pulled to GND) → address 0x70
// RESET pulled high to 3V3 (no MCU control)
// Each downstream channel has one SHT sensor (J1–J8)
#define TCA9548A_ADDR       0x70
#define SHT45_ADDR          0x44    // fixed — no address pins
#define SHT_MUX_CH0        0       // J1 — SHT sensor 1
#define SHT_MUX_CH1        1       // J2 — SHT sensor 2
#define SHT_MUX_CH2        2       // J3 — SHT sensor 3
#define SHT_MUX_CH3        3       // J4 — SHT sensor 4
#define SHT_MUX_CH4        4       // J5 — SHT sensor 5
#define SHT_MUX_CH5        5       // J6 — SHT sensor 6
#define SHT_MUX_CH6        6       // J7 — SHT sensor 7
#define SHT_MUX_CH7        7       // J8 — SHT sensor 8

// -----------------------------------------------------------------------------
// Battery Measurement — MAX17048 Fuel Gauge (I2C, fixed address)
// VDD sourced from +BATT rail — absent without battery connected
// -----------------------------------------------------------------------------
#define MAX17048_ADDR   0x36

// -----------------------------------------------------------------------------
// MOSFET Switching Board — PCF8575 GPIO Expander (I2C)
// Address set by A0/A1/A2 strapping (0x20–0x27 range)
// Port 0: P00–P07 (byte 0), Port 1: P10–P17 (byte 1)
//
// Board 1: JP1+JP2 (CH0) and JP3+JP4 (CH1) closed
// Board 2: JP5+JP6 (CH2) and JP7+JP8 (CH3) closed
// Both JP lines per channel are closed — drive both bits together to activate.
//   CH0 (MosfetSwitch)  — P01 (JP1, Stack_Bot_1) + P02 (JP2, Stack_Bot_2)
//   CH1 (MosfetSwitch1) — P03 (JP3, Stack_Bot_3) + P04 (JP4, Stack_Bot_4)
//   CH2 (MosfetSwitch2) — P05 (JP5, Stack_Bot_5) + P06 (JP6, Stack_Bot_6)
//   CH3 (MosfetSwitch3) — P07 (JP7, Stack_Bot_7) + P10 (JP8, Stack_Bot_8)
// Power rails on J3: +24V (Bot_9–13), +5V (Bot_14–15), GND (Bot_16–20)
// -----------------------------------------------------------------------------
#define PCF8575_ADDR        0x20
// Port 0 masks (byte 0 in Wire.write)
#define MOSFET_CH0_P0       0x06    // P01 | P02 — Board 1, CH0
#define MOSFET_CH1_P0       0x18    // P03 | P04 — Board 1, CH1
#define MOSFET_CH2_P0       0x60    // P05 | P06 — Board 2, CH2
#define MOSFET_CH3_P0       0x80    // P07       — Board 2, CH3 (port 0 part)
// Port 1 masks (byte 1 in Wire.write)
#define MOSFET_CH3_P1       0x01    // P10       — Board 2, CH3 (port 1 part)

// -----------------------------------------------------------------------------
// RS-485 Half-Duplex (Serial2)
// -----------------------------------------------------------------------------
#define RS485_TX    41              // -> MAX485 DI (driver input)
#define RS485_RX    47              // <- MAX485 RO (receiver output)
#define RS485_DIR   42              // MAX485 DE/RE: HIGH = TX, LOW = RX

// -----------------------------------------------------------------------------
// Status LEDs (active-HIGH, 280 Ohm series resistors)
// -----------------------------------------------------------------------------
#define LED_HB      38              // D13 — Heartbeat
#define LED_SD      39              // D14 — SD card present
#define LED_WIFI    40              // D15 — WiFi connected
#define LED_FLT     48              //      — Major fault
