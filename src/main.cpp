#include <Arduino.h>
#include <SPI.h>
#include <Adafruit_MAX31865.h>

#include "pin_definitions.h"

#define PT1000_R_NOM    1000.0f
#define PT1000_R_REF    4000.0f

static Adafruit_MAX31865 g_pt[4] = {
    Adafruit_MAX31865(PT1000_B1_CH1_CS, SPI_MOSI, SPI_MISO, SPI_SCK),
    Adafruit_MAX31865(PT1000_B1_CH2_CS, SPI_MOSI, SPI_MISO, SPI_SCK),
    Adafruit_MAX31865(PT1000_B2_CH1_CS, SPI_MOSI, SPI_MISO, SPI_SCK),
    Adafruit_MAX31865(PT1000_B2_CH2_CS, SPI_MOSI, SPI_MISO, SPI_SCK),
};

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("PT1000 test — 3-wire MAX31865, R_nom=1000, R_ref=4300");

    for (auto &p : g_pt) p.begin(MAX31865_3WIRE);
}

void loop() {
    Serial.printf("[%.1fs]", millis() / 1000.0f);
    for (uint8_t i = 0; i < 4; i++) {
        uint8_t fault = g_pt[i].readFault();
        if (fault) {
            g_pt[i].clearFault();
            Serial.printf("  CH%d: FAULT 0x%02X", i, fault);
        } else {
            float t = g_pt[i].temperature(PT1000_R_NOM, PT1000_R_REF);
            Serial.printf("  CH%d: %.2f C", i, t);
        }
    }
    Serial.println();
    delay(1000);
}
