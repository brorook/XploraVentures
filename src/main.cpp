#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>

// ── Adjust these to match your schematic pin assignments ──
#define I2C_SDA   8
#define I2C_SCL   9

// Status LEDs (active-high, each with 280Ω series resistor)
#define LED_HB    38   // D13 — Heartbeat
#define LED_SD    39   // D14 — SD card present
#define LED_WIFI  40   // D15 — WiFi connected
#define LED_FLT   48   // Major fault

// SD card
#define SD_DETECT 13   // active-low: LOW = card present (R24 10k pull-up to +3V3)

// MAX17048 (fuel gauge) - fixed address
#define MAX17048_ADDR  0x36
#define REG_VCELL      0x02
#define REG_SOC        0x04
#define REG_VERSION    0x08

// PCF8575 (GPIO expander) - depends on A0/A1/A2 strapping
#define PCF8575_ADDR   0x20

// ─────────────────────────────────────────────────────────

static bool max17048Present = false;

void printSeparator() {
  Serial.println("─────────────────────────────────");
}

// ── I2C scan ─────────────────────────────────────────────
void i2cScan() {
  printSeparator();
  Serial.println("TEST 1: I2C Bus Scan");
  printSeparator();
  int found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  Found device at 0x%02X", addr);
      if (addr == MAX17048_ADDR) Serial.print("  ← MAX17048 (fuel gauge)");
      if (addr == PCF8575_ADDR)  Serial.print("  ← PCF8575 (GPIO expander)");
      Serial.println();
      found++;
    }
  }
  Serial.printf("  Total: %d device(s) found\n", found);
}

// ── MAX17048 ─────────────────────────────────────────────
uint16_t max17048ReadReg(uint8_t reg) {
  Wire.beginTransmission(MAX17048_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom(MAX17048_ADDR, 2);
  return (Wire.read() << 8) | Wire.read();
}

void testMAX17048() {
  printSeparator();
  Serial.println("TEST 2: MAX17048 Fuel Gauge");
  printSeparator();

  Wire.beginTransmission(MAX17048_ADDR);
  if (Wire.endTransmission() != 0) {
    Serial.println("  ERROR: MAX17048 not found on I2C");
    Serial.println("  NOTE: U2 VDD is sourced from +BATT — chip is unpowered without a battery");
    return;
  }
  max17048Present = true;

  uint16_t vcellRaw = max17048ReadReg(REG_VCELL);
  uint16_t socRaw   = max17048ReadReg(REG_SOC);
  uint16_t version  = max17048ReadReg(REG_VERSION);

  float vcell = vcellRaw * (78.125f / 1000000.0f);  // 78.125μV per LSB per MAX17048 datasheet
  float soc   = socRaw / 256.0f;               // 1/256 % per LSB

  Serial.printf("  Version : 0x%04X\n", version);
  Serial.printf("  VCELL   : %.3f V\n", vcell);
  Serial.printf("  SoC     : %.1f %%\n", soc);

  if (vcell < 3.0f)  Serial.println("  ⚠ VCELL low — battery flat or not connected");
  if (vcell > 4.25f) Serial.println("  ⚠ VCELL high — check sense wiring");
  if (soc   > 100.f) Serial.println("  ⚠ SoC out of range — may need quick-start");
}

// ── PCF8575 ──────────────────────────────────────────────
void testPCF8575() {
  printSeparator();
  Serial.println("TEST 3: PCF8575 GPIO Expander");
  printSeparator();

  Wire.beginTransmission(PCF8575_ADDR);
  if (Wire.endTransmission() != 0) {
    Serial.println("  ERROR: PCF8575 not found — check address strapping (A0/A1/A2)");
    Serial.println("  Try addresses 0x20–0x27");
    return;
  }

  // Read current state of all 16 pins
  Wire.requestFrom(PCF8575_ADDR, 2);
  uint8_t lo = Wire.read();
  uint8_t hi = Wire.read();
  Serial.printf("  Port 0 (P00–P07): 0x%02X\n", lo);
  Serial.printf("  Port 1 (P10–P17): 0x%02X\n", hi);

  // Blink all outputs LOW then back HIGH
  Serial.println("  Toggling all outputs LOW...");
  Wire.beginTransmission(PCF8575_ADDR);
  Wire.write(0x00); Wire.write(0x00);
  Wire.endTransmission();
  delay(500);

  Serial.println("  Toggling all outputs HIGH...");
  Wire.beginTransmission(PCF8575_ADDR);
  Wire.write(0xFF); Wire.write(0xFF);
  Wire.endTransmission();
  delay(500);

  Serial.println("  PCF8575 OK");
}

// ── WiFi scan ────────────────────────────────────────────
void testWiFi() {
  printSeparator();
  Serial.println("TEST 4: WiFi RF Scan");
  printSeparator();
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);

  Serial.println("  Scanning...");
  int n = WiFi.scanNetworks();
  if (n == 0) {
    Serial.println("  No networks found — check antenna");
    digitalWrite(LED_WIFI, LOW);
  } else {
    Serial.printf("  Found %d network(s):\n", n);
    for (int i = 0; i < min(n, 5); i++) {
      Serial.printf("  [%d] %s  RSSI: %d dBm  Ch: %d\n",
        i+1, WiFi.SSID(i).c_str(), WiFi.RSSI(i), WiFi.channel(i));
    }
    digitalWrite(LED_WIFI, HIGH);
  }
  WiFi.scanDelete();
}

// ── Heap / chip info ─────────────────────────────────────
void testChipInfo() {
  printSeparator();
  Serial.println("TEST 5: Chip Info");
  printSeparator();
  Serial.printf("  Chip model  : %s\n",  ESP.getChipModel());
  Serial.printf("  CPU freq    : %d MHz\n", ESP.getCpuFreqMHz());
  Serial.printf("  Flash size  : %d KB\n", ESP.getFlashChipSize() / 1024);
  Serial.printf("  Free heap   : %d bytes\n", ESP.getFreeHeap());
  Serial.printf("  MAC address : %s\n",  WiFi.macAddress().c_str());
}

// ─────────────────────────────────────────────────────────

void testLED() {
  printSeparator();
  Serial.println("TEST 0: Status LEDs");
  printSeparator();

  const struct { int pin; const char* name; } leds[] = {
    { LED_HB,   "GPIO38 HB   (D13)" },
    { LED_SD,   "GPIO39 SD   (D14)" },
    { LED_WIFI, "GPIO40 WiFi (D15)" },
    { LED_FLT,  "GPIO48 FLT      " },
  };

  for (auto& l : leds) {
    pinMode(l.pin, OUTPUT);
    digitalWrite(l.pin, LOW);
  }

  for (auto& l : leds) {
    Serial.printf("  %s — ON\n", l.name);
    digitalWrite(l.pin, HIGH); delay(400);
    digitalWrite(l.pin, LOW);  delay(150);
  }
  Serial.println("  LED sweep done — verify each lit in sequence");
}

// ── SD card detect ────────────────────────────────────────
bool sdCardPresent() {
  return digitalRead(SD_DETECT) == LOW;  // active-low
}

void testSDDetect() {
  printSeparator();
  Serial.println("TEST 6: SD Card Detect (GPIO13)");
  printSeparator();
  pinMode(SD_DETECT, INPUT);  // external 10k pull-up on R24
  bool present = sdCardPresent();
  Serial.printf("  GPIO13 raw: %s\n", present ? "LOW" : "HIGH");
  Serial.printf("  SD card   : %s\n", present ? "PRESENT" : "NOT PRESENT");
  digitalWrite(LED_SD, present ? HIGH : LOW);
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  Serial.println("\n\n╔══════════════════════════════╗");
  Serial.println("║  XploraVentures Bring-Up     ║");
  Serial.println("╚══════════════════════════════╝");

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);  // 100 kHz for bring-up — slower is safer

  testLED();
  testWiFi();   // init WiFi.mode first so macAddress() is valid
  testChipInfo();
  i2cScan();
  testMAX17048();
  testPCF8575();
  testSDDetect();

  printSeparator();
  Serial.println("Bring-up complete. Looping VCELL + HB blink every 5s...");
}

void loop() {
  // Heartbeat blink — 250 ms on so it's clearly visible
  digitalWrite(LED_HB, HIGH); delay(250);
  digitalWrite(LED_HB, LOW);

  // SD card detect — update every loop iteration
  digitalWrite(LED_SD, sdCardPresent() ? HIGH : LOW);

  if (!max17048Present) {
    // No battery connected — not a runtime fault, just absent hardware
    Serial.printf("[%.1fs] MAX17048 absent (no battery)\n", millis() / 1000.0f);
    delay(4750);
    return;
  }

  uint16_t vcellRaw = max17048ReadReg(REG_VCELL);
  float vcell = vcellRaw * (78.125f / 1000000.0f);
  bool flt = (vcell < 3.0f || vcell > 4.25f);
  digitalWrite(LED_FLT, flt ? HIGH : LOW);
  Serial.printf("[%.1fs] VCELL = %.3f V%s\n",
    millis() / 1000.0f, vcell, flt ? "  ⚠" : "");
  delay(4750);
}