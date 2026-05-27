#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include <Adafruit_MAX31865.h>   // PT1000 via MAX31865 — change if different ADC chip

#include "pin_definitions.h"
#include "kcs208.h"

// ── Version ───────────────────────────────────────────────────────────────────
#define FW_VERSION      "1.1.0"

// ── Timing ────────────────────────────────────────────────────────────────────
#define TELEMETRY_MS    2000
#define KCS208_MS       5000

// ── PT1000 config ─────────────────────────────────────────────────────────────
#define PT1000_R_NOM    1000.0f
#define PT1000_R_REF    4300.0f  // verify against MAX31865 board reference resistor

// ── Sensor state ──────────────────────────────────────────────────────────────
static float    g_sht_t[8]   = {};
static float    g_sht_h[8]   = {};
static float    g_pt_t[4]    = {};
static float    g_batt_v     = 0.0f;
static float    g_batt_soc   = 0.0f;
static bool     g_mosfet[8]  = {};
static int16_t  g_kcs_pv     = 0;
static int16_t  g_kcs_sv     = 0;
static uint16_t g_kcs_mv     = 0;
static bool     g_kcs_run    = false;
static uint16_t g_kcs_status = 0;
static uint8_t  g_pcf_p0     = 0x00;
static uint8_t  g_pcf_p1     = 0x00;

// ── SD state ──────────────────────────────────────────────────────────────────
static bool  g_sdReady   = false;
static bool  g_sdLogging = false;
static File  g_sdFile;

// ── Misc ──────────────────────────────────────────────────────────────────────
static String g_rxBuf;

static Adafruit_MAX31865 g_pt[4] = {
    Adafruit_MAX31865(PT1000_B1_CH1_CS, SPI_MOSI, SPI_MISO, SPI_SCK),
    Adafruit_MAX31865(PT1000_B1_CH2_CS, SPI_MOSI, SPI_MISO, SPI_SCK),
    Adafruit_MAX31865(PT1000_B2_CH1_CS, SPI_MOSI, SPI_MISO, SPI_SCK),
    Adafruit_MAX31865(PT1000_B2_CH2_CS, SPI_MOSI, SPI_MISO, SPI_SCK),
};

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

static void readAllSHT45() {
    for (uint8_t ch = 0; ch < 8; ch++) {
        muxSelect(ch);
        delay(2);
        sht45Read(g_sht_t[ch], g_sht_h[ch]);  // resets to 0.0 on failure
    }
    muxDeselect();
}

// =============================================================================
// PT1000
// =============================================================================

static void readAllPT1000() {
    for (uint8_t i = 0; i < 4; i++)
        g_pt_t[i] = g_pt[i].temperature(PT1000_R_NOM, PT1000_R_REF);
}

// =============================================================================
// MAX17048 battery
// =============================================================================

static uint16_t max17048Reg(uint8_t reg) {
    Wire.beginTransmission(MAX17048_ADDR);
    Wire.write(reg);
    Wire.endTransmission(false);
    Wire.requestFrom((uint8_t)MAX17048_ADDR, (uint8_t)2);
    return ((uint16_t)Wire.read() << 8) | Wire.read();
}

static void readBattery() {
    Wire.beginTransmission(MAX17048_ADDR);
    if (Wire.endTransmission() != 0) return;
    g_batt_v   = max17048Reg(0x02) * (78.125f / 1000000.0f);
    g_batt_soc = max17048Reg(0x04) / 256.0f;
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
    if (ch > 7) return;
    struct { uint8_t *port; uint8_t mask; } lut[8] = {
        { &g_pcf_p0, MOSFET_CH0_P0 },
        { &g_pcf_p0, MOSFET_CH1_P0 },
        { &g_pcf_p0, MOSFET_CH2_P0 },
        { &g_pcf_p0, MOSFET_CH3_P0 },
        { &g_pcf_p0, MOSFET_CH4_P0 },
        { &g_pcf_p0, MOSFET_CH5_P0 },
        { &g_pcf_p0, MOSFET_CH6_P0 },
        { &g_pcf_p1, MOSFET_CH7_P1 },
    };
    if (on) *lut[ch].port |=  lut[ch].mask;
    else    *lut[ch].port &= ~lut[ch].mask;
    g_mosfet[ch] = on;
    pcfFlush();
}

// =============================================================================
// KCS208 Modbus RTU
// =============================================================================

static uint16_t crc16(const uint8_t *d, uint8_t n) {
    uint16_t crc = 0xFFFF;
    for (uint8_t i = 0; i < n; i++) {
        crc ^= d[i];
        for (uint8_t b = 0; b < 8; b++)
            crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : crc >> 1;
    }
    return crc;
}

static uint16_t modbusReadReg(uint16_t reg, bool &ok) {
    uint8_t req[8] = { KCS208_MODBUS_ADDR, MODBUS_FC_READ_REG,
                       (uint8_t)(reg >> 8), (uint8_t)(reg), 0x00, 0x01, 0, 0 };
    uint16_t c = crc16(req, 6); req[6] = c; req[7] = c >> 8;
    while (Serial2.available()) Serial2.read();
    digitalWrite(RS485_DIR, HIGH); delayMicroseconds(100);
    Serial2.write(req, 8); Serial2.flush();
    delayMicroseconds(100); digitalWrite(RS485_DIR, LOW);
    uint8_t resp[7] = {};
    uint8_t n = (uint8_t)Serial2.readBytes(resp, 7);
    ok = (n == 7 && resp[1] == MODBUS_FC_READ_REG);
    return ((uint16_t)resp[3] << 8) | resp[4];
}

static void modbusWriteReg(uint16_t reg, uint16_t val) {
    uint8_t req[8] = { KCS208_MODBUS_ADDR, MODBUS_FC_WRITE_REG,
                       (uint8_t)(reg >> 8), (uint8_t)(reg),
                       (uint8_t)(val >> 8), (uint8_t)(val), 0, 0 };
    uint16_t c = crc16(req, 6); req[6] = c; req[7] = c >> 8;
    digitalWrite(RS485_DIR, HIGH); delayMicroseconds(100);
    Serial2.write(req, 8); Serial2.flush();
    delayMicroseconds(100); digitalWrite(RS485_DIR, LOW);
}

static void pollKCS208() {
    bool ok; uint16_t raw;
    raw = modbusReadReg(KCS208_REG_PV, ok);
    if (ok) g_kcs_pv = (raw > 32767) ? (int16_t)(raw - 65536) : (int16_t)raw;
    raw = modbusReadReg(KCS208_REG_SV, ok);
    if (ok) g_kcs_sv = (raw > 32767) ? (int16_t)(raw - 65536) : (int16_t)raw;
    raw = modbusReadReg(KCS208_REG_MV,     ok); if (ok) g_kcs_mv     = raw;
    raw = modbusReadReg(KCS208_REG_RUN,    ok); if (ok) g_kcs_run    = (raw == 0);
    raw = modbusReadReg(KCS208_REG_STATUS, ok); if (ok) g_kcs_status = raw;
}

// =============================================================================
// WiFi
// =============================================================================

static void connectWiFi(const char *ssid, const char *pass) {
    WiFi.begin(ssid, pass);
    for (uint8_t i = 0; i < 20 && WiFi.status() != WL_CONNECTED; i++) delay(500);
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("{\"wifi_ip\":\"%s\"}\n", WiFi.localIP().toString().c_str());
        digitalWrite(LED_WIFI, HIGH);
    } else {
        Serial.print("{\"wifi_ip\":null}\n");
        digitalWrite(LED_WIFI, LOW);
    }
}

// =============================================================================
// SD card logging
// =============================================================================

static bool sdInit() {
    if (digitalRead(SD_DETECT) != LOW) return false;   // no card
    SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI);
    return SD.begin(SD_CS, SPI, 4000000);
}

static void sdLogStart(const char *name = nullptr) {
    if (!g_sdReady) {
        Serial.print("{\"sd_err\":\"SD not ready\"}\n");
        return;
    }
    char path[32];
    if (name && strlen(name) > 0) {
        // Sanitise: keep alphanumeric, dash, underscore; max 20 chars
        char safe[21] = {};
        uint8_t j = 0;
        for (uint8_t i = 0; name[i] && j < 20; i++) {
            char c = name[i];
            if (isalnum(c) || c == '_' || c == '-') safe[j++] = c;
        }
        snprintf(path, sizeof(path), "/%s.CSV", safe[0] ? safe : "LOG");
        // Append counter if file already exists
        if (SD.exists(path)) {
            for (uint16_t n = 1; n <= 9999; n++) {
                snprintf(path, sizeof(path), "/%s_%04u.CSV", safe, n);
                if (!SD.exists(path)) break;
            }
        }
    } else {
        for (uint16_t n = 1; n <= 9999; n++) {
            snprintf(path, sizeof(path), "/LOG_%04u.CSV", n);
            if (!SD.exists(path)) break;
        }
    }
    g_sdFile = SD.open(path, FILE_WRITE);
    if (!g_sdFile) {
        Serial.print("{\"sd_err\":\"open failed\"}\n");
        return;
    }
    // Header
    g_sdFile.print("millis");
    for (uint8_t i = 0; i < 8; i++) g_sdFile.printf(",sht%d_t", i);
    for (uint8_t i = 0; i < 8; i++) g_sdFile.printf(",sht%d_h", i);
    for (uint8_t i = 0; i < 4; i++) g_sdFile.printf(",pt%d_t",  i);
    g_sdFile.print(",batt_v,batt_soc,mosfet0,mosfet1,mosfet2,mosfet3,mosfet4,mosfet5,mosfet6,mosfet7");
    g_sdFile.println(",kcs_pv,kcs_sv,kcs_mv,kcs_run");
    g_sdFile.flush();
    g_sdLogging = true;
    Serial.printf("{\"sd_log\":true,\"file\":\"%s\"}\n", path);
}

static void sdLogStop() {
    g_sdLogging = false;
    if (g_sdFile) { g_sdFile.close(); }
    Serial.print("{\"sd_log\":false}\n");
}

static void sdLogRow() {
    if (!g_sdLogging || !g_sdFile) return;
    g_sdFile.print(millis());
    for (uint8_t i = 0; i < 8; i++) g_sdFile.printf(",%.1f", g_sht_t[i]);
    for (uint8_t i = 0; i < 8; i++) g_sdFile.printf(",%.1f", g_sht_h[i]);
    for (uint8_t i = 0; i < 4; i++) g_sdFile.printf(",%.1f", g_pt_t[i]);
    g_sdFile.printf(",%.3f,%.1f", g_batt_v, g_batt_soc);
    for (uint8_t i = 0; i < 8; i++) g_sdFile.printf(",%d", g_mosfet[i] ? 1 : 0);
    g_sdFile.printf(",%d,%d,%d,%d\n", g_kcs_pv, g_kcs_sv, g_kcs_mv, g_kcs_run ? 1 : 0);
    g_sdFile.flush();
}

// =============================================================================
// Telemetry JSON
// =============================================================================

static void emitTelemetry() {
    JsonDocument doc;

    JsonArray sht = doc["sht"].to<JsonArray>();
    for (uint8_t i = 0; i < 8; i++) {
        JsonObject s = sht.add<JsonObject>();
        s["t"] = roundf(g_sht_t[i] * 10) / 10.0f;
        s["h"] = roundf(g_sht_h[i] * 10) / 10.0f;
    }
    JsonArray pt = doc["pt1000"].to<JsonArray>();
    for (uint8_t i = 0; i < 4; i++) {
        pt.add<JsonObject>()["t"] = roundf(g_pt_t[i] * 10) / 10.0f;
    }
    doc["batt"]["v"]   = roundf(g_batt_v   * 1000) / 1000.0f;
    doc["batt"]["soc"] = roundf(g_batt_soc * 10)   / 10.0f;
    JsonArray mos = doc["mosfet"].to<JsonArray>();
    for (uint8_t i = 0; i < 8; i++) mos.add(g_mosfet[i]);
    doc["kcs208"]["pv"]     = g_kcs_pv;
    doc["kcs208"]["sv"]     = g_kcs_sv;
    doc["kcs208"]["mv"]     = g_kcs_mv;
    doc["kcs208"]["run"]    = g_kcs_run;
    doc["kcs208"]["status"] = g_kcs_status;

    doc["sd"]["present"] = (digitalRead(SD_DETECT) == LOW);
    doc["sd"]["logging"] = g_sdLogging;
    doc["fw"]            = FW_VERSION;

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

    if      (strcmp(cmd, "mosfet")     == 0) setMosfet(doc["ch"].as<uint8_t>(), doc["on"].as<bool>());
    else if (strcmp(cmd, "kcs208_sv")  == 0) modbusWriteReg(KCS208_REG_SV,  doc["val"].as<uint16_t>());
    else if (strcmp(cmd, "kcs208_run") == 0) modbusWriteReg(KCS208_REG_RUN, doc["run"].as<bool>() ? 0 : 1);
    else if (strcmp(cmd, "wifi")       == 0) connectWiFi(doc["ssid"], doc["pass"]);
    else if (strcmp(cmd, "sd_log")     == 0) {
        if (doc["active"].as<bool>()) sdLogStart(doc["name"] | nullptr);
        else                          sdLogStop();
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

    for (auto &p : g_pt) p.begin(MAX31865_3WIRE);  // adjust: 2WIRE / 3WIRE / 4WIRE

    pinMode(RS485_DIR, OUTPUT);
    digitalWrite(RS485_DIR, LOW);
    Serial2.begin(KCS208_BAUD, SERIAL_8N1, RS485_RX, RS485_TX);
    Serial2.setTimeout(100);

    pinMode(SD_DETECT, INPUT);
    pinMode(LED_HB,    OUTPUT);
    pinMode(LED_SD,    OUTPUT);
    pinMode(LED_WIFI,  OUTPUT);
    pinMode(LED_FLT,   OUTPUT);

    pcfFlush();   // all MOSFETs off

    g_sdReady = sdInit();
    digitalWrite(LED_SD, g_sdReady ? HIGH : LOW);
}

void loop() {
    static uint32_t lastTelemetry = 0;
    static uint32_t lastKCS       = 0;
    static uint32_t lastSDCheck   = 0;
    static bool     lastDetect    = false;

    checkSerial();

    // SD card hot-plug check — every 10 s
    uint32_t now = millis();
    if (now - lastSDCheck >= 10000) {
        lastSDCheck = now;
        bool cardPresent = (digitalRead(SD_DETECT) == LOW);
        if (cardPresent && !lastDetect) {
            g_sdReady = sdInit();
        }
        if (!cardPresent && lastDetect && g_sdLogging) {
            sdLogStop();   // card pulled mid-log
            g_sdReady = false;
        }
        lastDetect = cardPresent;
        digitalWrite(LED_SD, cardPresent ? HIGH : LOW);
    }

    if (now - lastTelemetry >= TELEMETRY_MS) {
        lastTelemetry = now;
        readAllSHT45();
        readAllPT1000();
        readBattery();

        digitalWrite(LED_HB,  !digitalRead(LED_HB));
        digitalWrite(LED_FLT, (g_batt_v > 0.5f && (g_batt_v < 3.0f || g_batt_v > 4.25f)) ? HIGH : LOW);

        emitTelemetry();
        sdLogRow();
    }

    if (now - lastKCS >= KCS208_MS) {
        lastKCS = now;
        pollKCS208();
    }
}
