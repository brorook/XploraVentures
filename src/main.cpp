#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <Adafruit_MAX31865.h>
#include <ArduinoJson.h>
#include <SensirionI2cSht4x.h>
#include <ModbusMaster.h>

#include "pin_definitions.h"

// ── Version ───────────────────────────────────────────────────────────────────
#define FW_VERSION      "1.4.0-ar"

// ── Timing ────────────────────────────────────────────────────────────────────
#define TELEMETRY_MS    2000

// ── PFLOW3008 RS485 ───────────────────────────────────────────────────────────
#define PFLOW_BAUD      38400
#define PFLOW_SLAVE_ID  1
#define REG_FLOW_HI     0x003A

// ── Heater setpoint and hysteresis (°C) ──────────────────────────────────────
static float g_setpoint   = 0.0f;
static float g_hysteresis = 0.5f;

// ── PT1000 ────────────────────────────────────────────────────────────────────
#define PT1000_R_NOM    1000.0f
#define PT1000_R_REF    4000.0f
static Adafruit_MAX31865 g_rtd(PT1000_B2_CH1_CS, SPI_MOSI, SPI_MISO, SPI_SCK);
static float    g_rtd_temp  = NAN;
static uint8_t  g_rtd_fault = 0;

// ── Sensor state ──────────────────────────────────────────────────────────────
static float g_t1 = 0.0f, g_h1 = 0.0f;   // SHT45 Channel 1 (mux 0)
static float g_t3 = 0.0f, g_h3 = 0.0f;   // SHT45 Channel 3 (mux 2)
static bool  g_heater    = false;           // MOSFET CH0 — coil heater
static bool  g_solenoid  = false;           // MOSFET CH1 — solenoid 1
static bool  g_solenoid2 = false;           // MOSFET CH2 — solenoid 2

// ── Misc ──────────────────────────────────────────────────────────────────────
static String             g_rxBuf;
static uint8_t            g_pcf_p0 = 0x00;
static uint8_t            g_pcf_p1 = 0x00;
static SensirionI2cSht4x  g_sht4x;
static ModbusMaster       g_pflow;
static float              g_flow_slpm = NAN;

// =============================================================================
// PFLOW3008 — RS485 Modbus RTU
// =============================================================================

static void pfPreTx()  { digitalWrite(RS485_DIR, HIGH); }
static void pfPostTx() { digitalWrite(RS485_DIR, LOW);  }

static void readFlow() {
    uint8_t res = g_pflow.readHoldingRegisters(REG_FLOW_HI, 2);
    if (res == g_pflow.ku8MBSuccess) {
        uint32_t raw = ((uint32_t)g_pflow.getResponseBuffer(0) << 16)
                      | g_pflow.getResponseBuffer(1);
        g_flow_slpm = raw / 1000.0f;
    } else {
        g_flow_slpm = NAN;
    }
}

// =============================================================================
// PCF8575 / MOSFET
// =============================================================================

static void pcfFlush() {
    Wire.beginTransmission(PCF8575_ADDR);
    Wire.write(g_pcf_p0); Wire.write(g_pcf_p1);
    Wire.endTransmission();
}

static void setMosfet(uint8_t ch, bool on) {
    struct { uint8_t *port; uint8_t mask; } lut[3] = {
        { &g_pcf_p0, MOSFET_CH0_P0 },
        { &g_pcf_p0, MOSFET_CH1_P0 },
        { &g_pcf_p0, MOSFET_CH2_P0 },
    };
    if (ch > 2) return;
    if (on) *lut[ch].port |=  lut[ch].mask;
    else    *lut[ch].port &= ~lut[ch].mask;
    pcfFlush();
    if (ch == 0) g_heater    = on;
    if (ch == 1) g_solenoid  = on;
    if (ch == 2) g_solenoid2 = on;
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

static bool sht4xRead(float &temp, float &hum) {
    float t, h;
    uint16_t err = g_sht4x.measureHighPrecision(t, h);
    if (err) { temp = 0.0f; hum = 0.0f; return false; }
    temp = t;
    hum  = constrain(h, 0.0f, 100.0f);
    return true;
}

static void readPT1000() {
    g_rtd_fault = g_rtd.readFault();
    if (g_rtd_fault) {
        g_rtd.clearFault();
        g_rtd_temp = NAN;
    } else {
        g_rtd_temp = g_rtd.temperature(PT1000_R_NOM, PT1000_R_REF);
    }
}

static void readSensors() {
    muxSelect(0); delay(2); sht4xRead(g_t1, g_h1);
    muxSelect(2); delay(2); sht4xRead(g_t3, g_h3);
    muxDeselect();
    readPT1000();
    readFlow();
}

// =============================================================================
// Heater control — on/off thermostat with hysteresis
// =============================================================================

static void updateHeater() {
    if (isnan(g_rtd_temp)) { setMosfet(0, false); return; }  // RTD fault → heater off
    if (!g_heater && g_rtd_temp < g_setpoint - g_hysteresis) setMosfet(0, true);
    if ( g_heater && g_rtd_temp >= g_setpoint)               setMosfet(0, false);
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
    if (isnan(g_rtd_temp)) doc["rtd"] = nullptr;
    else                   doc["rtd"] = roundf(g_rtd_temp * 10) / 10.0f;
    doc["heater"]     = g_heater;
    doc["solenoid"]   = g_solenoid;
    doc["solenoid2"]  = g_solenoid2;
    doc["setpoint"]   = roundf(g_setpoint   * 10) / 10.0f;
    doc["hysteresis"] = roundf(g_hysteresis * 10) / 10.0f;
    if (isnan(g_flow_slpm)) doc["flow_slpm"] = nullptr;
    else                    doc["flow_slpm"] = roundf(g_flow_slpm * 1000) / 1000.0f;
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

    if      (strcmp(cmd, "set_sp")    == 0) g_setpoint   = doc["val"].as<float>();
    else if (strcmp(cmd, "set_hyst") == 0) g_hysteresis = max(0.0f, doc["val"].as<float>());
    else if (strcmp(cmd, "solenoid") == 0) setMosfet(1, doc["on"].as<bool>());
    else if (strcmp(cmd, "solenoid2")== 0) setMosfet(2, doc["on"].as<bool>());
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
    g_sht4x.begin(Wire, SHT45_ADDR);

    g_rtd.begin(MAX31865_3WIRE);

    pinMode(RS485_DIR, OUTPUT);
    digitalWrite(RS485_DIR, LOW);
    Serial2.begin(PFLOW_BAUD, SERIAL_8N1, RS485_RX, RS485_TX);
    g_pflow.begin(PFLOW_SLAVE_ID, Serial2);
    g_pflow.preTransmission(pfPreTx);
    g_pflow.postTransmission(pfPostTx);

    pinMode(LED_HB,   OUTPUT);
    pinMode(LED_SD,   OUTPUT); digitalWrite(LED_SD,   LOW);
    pinMode(LED_WIFI, OUTPUT); digitalWrite(LED_WIFI, LOW);
    pinMode(LED_FLT,  OUTPUT); digitalWrite(LED_FLT,  LOW);

    pcfFlush();   // all MOSFETs off
}

void loop() {
    static uint32_t lastTelemetry = 0;

    checkSerial();

    uint32_t now = millis();
    if (now - lastTelemetry >= TELEMETRY_MS) {
        lastTelemetry = now;
        readSensors();
        updateHeater();
        digitalWrite(LED_HB, !digitalRead(LED_HB));
        emitTelemetry();
    }
}
