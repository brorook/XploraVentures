#include <Arduino.h>
#include <Wire.h>
#include <ArduinoJson.h>
#include <PID_v1.h>

#include "pin_definitions.h"

// ── Version ───────────────────────────────────────────────────────────────────
#define FW_VERSION      "1.1.0-ar"

// ── Timing ────────────────────────────────────────────────────────────────────
#define TELEMETRY_MS    2000
#define PID_WINDOW_MS   5000

// ── PID tuning (from step-response, SIMC integrating process) ─────────────────
static double g_kp = 15.0, g_ki = 0.119, g_kd = 0.0;

// ── Heater setpoint (°C) ──────────────────────────────────────────────────────
static float g_setpoint = 30.0f;

// ── Sensor state ──────────────────────────────────────────────────────────────
static float g_t1 = 0.0f, g_h1 = 0.0f;   // SHT45 Channel 1 (mux 0)
static float g_t3 = 0.0f, g_h3 = 0.0f;   // SHT45 Channel 3 (mux 2)
static bool  g_heater   = false;           // MOSFET CH0 — coil heater
static bool  g_solenoid = false;           // MOSFET CH1 — solenoid

// ── PID ───────────────────────────────────────────────────────────────────────
static double g_pid_input    = 0.0;
static double g_pid_output   = 0.0;
static double g_pid_setpoint = 30.0;
static PID    g_pid(&g_pid_input, &g_pid_output, &g_pid_setpoint,
                    g_kp, g_ki, g_kd, DIRECT);

// ── Misc ──────────────────────────────────────────────────────────────────────
static String    g_rxBuf;
static uint8_t   g_pcf_p0 = 0x00;
static uint8_t   g_pcf_p1 = 0x00;

// =============================================================================
// PCF8575 / MOSFET
// =============================================================================

static void pcfFlush() {
    Wire.beginTransmission(PCF8575_ADDR);
    Wire.write(g_pcf_p0); Wire.write(g_pcf_p1);
    Wire.endTransmission();
}

static void setMosfet(uint8_t ch, bool on) {
    struct { uint8_t *port; uint8_t mask; } lut[2] = {
        { &g_pcf_p0, MOSFET_CH0_P0 },
        { &g_pcf_p0, MOSFET_CH1_P0 },
    };
    if (ch > 1) return;
    if (on) *lut[ch].port |=  lut[ch].mask;
    else    *lut[ch].port &= ~lut[ch].mask;
    pcfFlush();
    if (ch == 0) g_heater   = on;
    if (ch == 1) g_solenoid = on;
}

// =============================================================================
// TCA9548A
// =============================================================================

static void muxSelect(uint8_t ch) {
    Wire.beginTransmission(TCA9548A_ADDR);
    Wire.write(1 << ch);
    Wire.endTransmission();
}

static void muxDeselect() {
    Wire.beginTransmission(TCA9548A_ADDR);
    Wire.write(0x00);
    Wire.endTransmission();
}

// =============================================================================
// SHT45
// =============================================================================

static bool sht45Read(float &temp, float &hum) {
    Wire.beginTransmission(SHT45_ADDR);
    Wire.write(0xFD);
    if (Wire.endTransmission() != 0) { temp = 0.0f; hum = 0.0f; return false; }
    delay(10);
    Wire.requestFrom((uint8_t)SHT45_ADDR, (uint8_t)6);
    if (Wire.available() < 6) { temp = 0.0f; hum = 0.0f; return false; }
    uint8_t b[6]; for (auto &x : b) x = Wire.read();
    uint16_t t_raw = ((uint16_t)b[0] << 8) | b[1];
    uint16_t h_raw = ((uint16_t)b[3] << 8) | b[4];
    temp = -45.0f + 175.0f * (t_raw / 65535.0f);
    hum  = constrain(100.0f * (h_raw / 65535.0f), 0.0f, 100.0f);
    return true;
}

static void readSensors() {
    muxSelect(0); delay(2); sht45Read(g_t1, g_h1);
    muxSelect(2); delay(2); sht45Read(g_t3, g_h3);
    muxDeselect();
}

// =============================================================================
// Heater control — PID + time-proportioning (5 s window)
// =============================================================================

static void updateHeater() {
    static uint32_t windowStart = 0;
    uint32_t now = millis();
    if (now - windowStart >= PID_WINDOW_MS) windowStart = now;
    bool on = (double)(now - windowStart) < (g_pid_output / 255.0 * PID_WINDOW_MS);
    if (on != g_heater) setMosfet(0, on);
}

// =============================================================================
// Telemetry JSON
// =============================================================================

static void emitTelemetry() {
    JsonDocument doc;
    doc["sht1"]["t"]  = roundf(g_t1 * 10) / 10.0f;
    doc["sht1"]["h"]  = roundf(g_h1 * 10) / 10.0f;
    doc["sht3"]["t"]  = roundf(g_t3 * 10) / 10.0f;
    doc["sht3"]["h"]  = roundf(g_h3 * 10) / 10.0f;
    doc["heater"]     = g_heater;
    doc["solenoid"]   = g_solenoid;
    doc["setpoint"]   = roundf(g_setpoint * 10) / 10.0f;
    doc["pid_pct"]    = (uint8_t)round(g_pid_output / 255.0 * 100);
    doc["pid"]["kp"]  = g_kp;
    doc["pid"]["ki"]  = g_ki;
    doc["pid"]["kd"]  = g_kd;
    doc["fw"]         = FW_VERSION;
    serializeJson(doc, Serial);
    Serial.print('\n');
}

// =============================================================================
// Command parser
// =============================================================================

static void handleCommand(const String &line) {
    JsonDocument doc;
    if (deserializeJson(doc, line) != DeserializationError::Ok) return;
    const char *cmd = doc["cmd"];
    if (!cmd) return;

    if      (strcmp(cmd, "set_sp")   == 0) g_setpoint = doc["val"].as<float>();
    else if (strcmp(cmd, "solenoid") == 0) setMosfet(1, doc["on"].as<bool>());
    else if (strcmp(cmd, "set_pid")  == 0) {
        g_kp = doc["kp"] | g_kp;
        g_ki = doc["ki"] | g_ki;
        g_kd = doc["kd"] | g_kd;
        g_pid.SetTunings(g_kp, g_ki, g_kd);
    }
}

static void checkSerial() {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n') {
            g_rxBuf.trim();
            if (g_rxBuf.length()) handleCommand(g_rxBuf);
            g_rxBuf = "";
        } else {
            g_rxBuf += c;
        }
    }
}

// =============================================================================
// Setup / Loop
// =============================================================================

void setup() {
    Serial.begin(115200);

    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.setClock(400000);

    pinMode(LED_HB,   OUTPUT);
    pinMode(LED_SD,   OUTPUT); digitalWrite(LED_SD,   LOW);
    pinMode(LED_WIFI, OUTPUT); digitalWrite(LED_WIFI, LOW);
    pinMode(LED_FLT,  OUTPUT); digitalWrite(LED_FLT,  LOW);

    g_pid.SetMode(AUTOMATIC);
    g_pid.SetOutputLimits(0, 255);
    g_pid.SetSampleTime(TELEMETRY_MS);

    pcfFlush();   // all MOSFETs off
}

void loop() {
    static uint32_t lastTelemetry = 0;

    checkSerial();

    uint32_t now = millis();
    if (now - lastTelemetry >= TELEMETRY_MS) {
        lastTelemetry = now;
        readSensors();
        g_pid_input    = g_t3;
        g_pid_setpoint = g_setpoint;
        digitalWrite(LED_HB, !digitalRead(LED_HB));
        emitTelemetry();
    }

    g_pid.Compute();
    updateHeater();
}
