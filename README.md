# XploraVentures Dashboard — User Manual

A local dashboard for monitoring and controlling your ESP32-based sensor system. Runs entirely on your PC — no internet connection required after first launch.

---

## Requirements

- Windows 10 or 11 (64-bit)
- ESP32 connected via USB
- An internet connection on first launch only (to load the Inter font from Google Fonts)

No Python installation required.

---

## Getting Started

1. Plug your ESP32 into a USB port on your PC.
2. Double-click **XploraVentures.exe**.
3. A terminal window will open — leave it running in the background.
4. Your browser will open automatically at `http://localhost:8080`.

> If the browser does not open, navigate to `http://localhost:8080` manually.

---

## Connecting to the ESP32

At the top of the dashboard is the **Serial Connection** bar.

1. Click **Refresh Ports** to scan for available COM ports.
2. Select your ESP32 from the **Port** dropdown (usually labelled `CP210x` or `CH340`).
3. Set the **Baud Rate** to match your firmware (default: `115200`).
4. Click **Connect**.

The status indicator in the top-right corner will turn green when connected. Sensor values will begin updating automatically.

To disconnect, click **Disconnect**.

---

## Dashboard Sections

### Header Bar

Always visible at the top.

| Indicator | Description |
|-----------|-------------|
| **SD** pill | SD card state on the ESP32 — `SD —` (absent), `SD OK` (present), `SD REC` (recording, red blink) |
| **Voltage / % bar** | Battery voltage and state of charge |
| **Refresh** | Average time between incoming data packets |
| **Connected / Disconnected** | Serial link status |

---

### Logging

**PC CSV** — records all sensor data to a `.csv` file on your PC.

1. Click **Start** to begin recording. The filename is shown next to the indicator dot.
2. Click **Stop** when done.
3. Click **Download CSV** to save the file.

**ESP32 SD Card** — instructs the ESP32 to log data directly to its SD card.

1. Optionally enter a **filename** (without extension). Leave blank for an auto-numbered file (`LOG_0001.CSV`, etc.).
2. Click **Start SD Log**. The ESP32 begins writing immediately.
3. Click **Stop SD Log** to finalise the file on the SD card.

---

### Heater Controller — KCS208

Displays live readings from the KCS208 PID heater controller.

| Field | Description |
|-------|-------------|
| **Process Value** | Current measured temperature (°C) |
| **Setpoint** | Target temperature (°C) |
| **Output** | Current heater power output (%) |
| **State** | `RUNNING` (green) or `STOPPED` |

**To change the setpoint:** enter a value in the *Setpoint (°C)* field and click **Set SV**.

**To start or stop the heater:** click **Run / Stop**.

> If the controller reads `NOT CONNECTED`, the KCS208 is not communicating with the ESP32.

---

### MOSFET Switches — PCF8575

Four independently controlled output channels, each driving a MOSFET valve or actuator.

- Toggle the switch next to each channel to turn it on or off.
- The state shown reflects what the ESP32 last reported — it updates automatically on each data packet.

| Channel | Board |
|---------|-------|
| CH0 | Board 1 |
| CH1 | Board 1 |
| CH2 | Board 2 |
| CH3 | Board 2 |

---

### Temperature & Humidity — SHT45

Up to 16 SHT45 sensors across two boards (8 per board).

- Each tile shows the current temperature and relative humidity.
- A greyed-out `—` means that channel has no sensor connected.

---

### High-Temperature RTD — PT1000

Up to 8 PT1000 resistance temperature detectors across two boards (4 per board).

- Each tile shows the current temperature in °C.
- A greyed-out `—` means the sensor is open-circuit or not connected.

---

## Updating ESP32 Firmware

You can flash new firmware directly from the dashboard over the existing USB connection — no Arduino IDE or PlatformIO required on the user's machine.

### Getting the firmware file

In PlatformIO, build your project and locate the compiled binary:

```
.pio/build/<env>/firmware.bin
```

### Flashing

1. Connect to the ESP32 using the **Serial Connection** controls.
2. Scroll to the **Firmware Update** card.
3. Drag and drop the `firmware.bin` file onto the upload zone, or click to browse.
4. Leave **Flash Address** as `0x10000` (standard PlatformIO app address).
5. Click **Flash Firmware**.

The log area shows real-time esptool output. The ESP32 reboots automatically when flashing is complete and the dashboard reconnects.

> **Do not close the dashboard or unplug the USB cable during flashing.**

---

## Sharing on a Local Network

Other devices on the same WiFi network can view the dashboard (read-only) at the address shown in the WiFi card, e.g.:

```
http://192.168.1.45:8080
```

Only the PC running the executable can send commands to the ESP32.

---

## Troubleshooting

**No COM ports appear**
- Make sure the USB cable supports data (not charge-only).
- Install the USB-to-serial driver for your ESP32 board: [CP210x](https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers) or [CH340](https://www.wch-ic.com/downloads/CH341SER_EXE.html).

**Dashboard opens but shows no data**
- Confirm the correct port and baud rate are selected.
- Verify the ESP32 is running firmware that outputs newline-delimited JSON.

**Windows Defender / antivirus blocks the .exe**
- This is common with PyInstaller executables. Click *More info → Run anyway* on the SmartScreen prompt, or add an exception in your antivirus.

**The terminal window shows an error and closes**
- Port 8080 may already be in use by another application. Close the other application or contact your system administrator.

---

## Closing the Dashboard

Close the terminal window (or press `Ctrl+C` inside it) to shut down the dashboard server. The browser tab will disconnect automatically.

---

## Updating the Software

When a new version is available, a green banner appears at the top of the dashboard:

> **Update available — v1.2.0 · Download**

Click **Download** to open the GitHub releases page and download the new `XploraVentures.exe`. Replace your existing file with the downloaded one — no uninstall needed.

The update check runs automatically in the background each time you launch the dashboard. It requires an internet connection; if you are offline the check is silently skipped.
