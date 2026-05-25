#pragma once

// =============================================================================
// KCS208 Temperature Controller — Modbus RTU Register Map
// Transport: RS-485 via Serial2 (see pin_definitions.h RS485_*)
// Protocol:  Modbus RTU, function codes 0x03 (read) / 0x06 (write single)
// =============================================================================

#define KCS208_MODBUS_ADDR      1       // default device address

// -----------------------------------------------------------------------------
// Process registers
// -----------------------------------------------------------------------------
#define KCS208_REG_SV           0x2000  // Setpoint (R/W, °C)
#define KCS208_REG_PV           0x2010  // Measured temperature (R, °C, signed)
#define KCS208_REG_MV           0x2011  // Output % (R, unsigned)
#define KCS208_REG_STATUS       0x2118  // Output status bitmask (R, unsigned)

// -----------------------------------------------------------------------------
// Control registers
// -----------------------------------------------------------------------------
#define KCS208_REG_RUN          0x2106  // Run/Stop: 0 = RUN, 1 = STOP
#define KCS208_REG_OT           0x2104  // Control mode

// -----------------------------------------------------------------------------
// PID registers
// -----------------------------------------------------------------------------
#define KCS208_REG_P            0x210A  // Proportional band
#define KCS208_REG_I            0x210B  // Integral time (seconds)
#define KCS208_REG_D            0x210C  // Derivative time (seconds)

// -----------------------------------------------------------------------------
// REG_STATUS bitmask (KCS208_REG_STATUS)
// -----------------------------------------------------------------------------
#define KCS208_STATUS_OUT1      (1 << 0)
#define KCS208_STATUS_AL1       (1 << 1)
#define KCS208_STATUS_AL2       (1 << 2)
#define KCS208_STATUS_AT        (1 << 3)
#define KCS208_STATUS_LLL       (1 << 4)
#define KCS208_STATUS_HHHH      (1 << 5)

// -----------------------------------------------------------------------------
// Modbus RTU frame constants
// -----------------------------------------------------------------------------
#define MODBUS_FC_READ_REG      0x03
#define MODBUS_FC_WRITE_REG     0x06
#define KCS208_BAUD             9600
