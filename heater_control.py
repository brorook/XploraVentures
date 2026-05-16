#!/usr/bin/env python3
"""
Simple terminal interface for KCS208 Temperature Controller
Communicates via RS485/Modbus RTU
"""

import struct
import serial
import serial.tools.list_ports
import time
import sys

# Modbus register addresses
REG_SV     = 0x2000  # Setpoint
REG_PV     = 0x2010  # Measured temperature (read only)
REG_MV     = 0x2011  # Output % (read only)
REG_STATUS = 0x2118  # Output status (read only)
REG_RUN    = 0x2106  # Run/Stop (0=RUN, 1=STOP)
REG_OT     = 0x2104  # Control mode
REG_P      = 0x210A  # Proportional band
REG_I      = 0x210B  # Integral time
REG_D      = 0x210C  # Derivative time

DEVICE_ADDR = 1  # Default Modbus address


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def build_read(addr, reg, count=1):
    msg = struct.pack('>BBHH', addr, 0x03, reg, count)
    crc = crc16(msg)
    return msg + struct.pack('<H', crc)


def build_write(addr, reg, value):
    msg = struct.pack('>BBHH', addr, 0x06, reg, value & 0xFFFF)
    crc = crc16(msg)
    return msg + struct.pack('<H', crc)


def read_register(ser, reg, signed=True):
    ser.reset_input_buffer()
    ser.write(build_read(DEVICE_ADDR, reg))
    time.sleep(0.1)
    resp = ser.read(7)
    if len(resp) < 7 or resp[1] != 0x03:
        return None
    raw = struct.unpack('>H', resp[3:5])[0]
    if signed and raw > 32767:
        raw -= 65536
    return raw


def write_register(ser, reg, value):
    ser.reset_input_buffer()
    ser.write(build_write(DEVICE_ADDR, reg, value))
    time.sleep(0.1)
    resp = ser.read(8)
    return len(resp) == 8 and resp[1] == 0x06


def list_ports():
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("  No serial ports found.")
    else:
        for i, p in enumerate(ports):
            print(f"  [{i}] {p.device} — {p.description}")
    return ports


def connect():
    print("\nAvailable serial ports:")
    ports = list_ports()

    port_input = input("\nEnter port name or index (e.g. /dev/ttyUSB0 or 0): ").strip()
    if port_input.isdigit():
        port_name = ports[int(port_input)].device
    else:
        port_name = port_input

    baud = input("Baud rate [9600]: ").strip() or "9600"

    try:
        ser = serial.Serial(
            port=port_name,
            baudrate=int(baud),
            bytesize=8,
            parity=serial.PARITY_NONE,
            stopbits=1,
            timeout=0.5
        )
        print(f"\nConnected to {port_name} at {baud} baud.")
        return ser
    except Exception as e:
        print(f"Failed to connect: {e}")
        return None


def decode_status(status):
    bits = {
        0: "OUT1", 1: "AL1", 2: "AL2", 3: "AT",
        4: "LLL", 5: "HHHH"
    }
    active = [name for bit, name in bits.items() if status & (1 << bit)]
    return ", ".join(active) if active else "OK"


def print_status(ser):
    pv = read_register(ser, REG_PV)
    sv = read_register(ser, REG_SV)
    mv = read_register(ser, REG_MV, signed=False)
    status = read_register(ser, REG_STATUS, signed=False)

    print("\n┌─────────────────────────────┐")
    print(f"│  PV (current):  {str(pv)+'°C':>10}   │")
    print(f"│  SV (setpoint): {str(sv)+'°C':>10}   │")
    print(f"│  Output (MV):   {str(mv)+'%':>10}   │")
    print(f"│  Status:  {decode_status(status):>18}   │")
    print("└─────────────────────────────┘")


def monitor(ser, interval=2):
    print(f"\nMonitoring every {interval}s — press Ctrl+C to stop\n")
    try:
        while True:
            pv = read_register(ser, REG_PV)
            sv = read_register(ser, REG_SV)
            mv = read_register(ser, REG_MV, signed=False)
            ts = time.strftime("%H:%M:%S")
            bar = "█" * (mv // 5) if mv is not None else ""
            print(f"[{ts}]  PV: {pv:>5}°C   SV: {sv:>5}°C   MV: {mv:>3}%  {bar}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


def print_menu():
    print("""
┌──────────────────────────────────┐
│     KCS208 Heater Controller     │
├──────────────────────────────────┤
│  s  - Read status                │
│  t  - Set target temperature     │
│  r  - Run / Stop toggle          │
│  m  - Monitor live readings      │
│  p  - Read PID parameters        │
│  P  - Set PID parameters         │
│  q  - Quit                       │
└──────────────────────────────────┘""")


def main():
    print("=" * 40)
    print("  KCS208 RS485 Temperature Controller")
    print("=" * 40)

    ser = connect()
    if not ser:
        sys.exit(1)

    while True:
        print_menu()
        cmd = input("Command: ").strip().lower()

        if cmd == 'q':
            print("Bye!")
            ser.close()
            break

        elif cmd == 's':
            print_status(ser)

        elif cmd == 't':
            current_sv = read_register(ser, REG_SV)
            print(f"Current setpoint: {current_sv}°C")
            try:
                new_sv = int(input("New setpoint (°C): ").strip())
                if write_register(ser, REG_SV, new_sv):
                    print(f"Setpoint updated to {new_sv}°C")
                else:
                    print("Write failed — check connection.")
            except ValueError:
                print("Invalid input.")

        elif cmd == 'r':
            current = read_register(ser, REG_RUN, signed=False)
            if current is None:
                print("Could not read controller state.")
                continue
            is_stopped = current == 1
            state = "STOPPED" if is_stopped else "RUNNING"
            print(f"Currently: {state}")
            action = "RUN" if is_stopped else "STOP"
            confirm = input(f"Switch to {action}? [y/N]: ").strip().lower()
            if confirm == 'y':
                val = 0 if is_stopped else 1
                if write_register(ser, REG_RUN, val):
                    print(f"Controller set to {action}.")
                else:
                    print("Write failed.")

        elif cmd == 'm':
            try:
                interval = float(input("Update interval in seconds [2]: ").strip() or "2")
            except ValueError:
                interval = 2
            monitor(ser, interval)

        elif cmd == 'p':
            p = read_register(ser, REG_P, signed=False)
            i = read_register(ser, REG_I, signed=False)
            d = read_register(ser, REG_D, signed=False)
            print(f"\n  P (proportional band): {p}")
            print(f"  I (integral time):     {i}s")
            print(f"  D (derivative time):   {d}s")

        elif cmd == 'P':
            print("Leave blank to keep current value.")
            try:
                p_val = input("  P (proportional band): ").strip()
                i_val = input("  I (integral time, s):  ").strip()
                d_val = input("  D (derivative time, s):").strip()
                if p_val:
                    write_register(ser, REG_P, int(p_val))
                if i_val:
                    write_register(ser, REG_I, int(i_val))
                if d_val:
                    write_register(ser, REG_D, int(d_val))
                print("PID parameters updated.")
            except ValueError:
                print("Invalid input.")

        else:
            print("Unknown command.")


if __name__ == "__main__":
    main()