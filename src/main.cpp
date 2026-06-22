#include <Arduino.h>
#include <Wire.h>
#include <ModbusMaster.h>

#include "pin_definitions.h"

// PFLOW3008 defaults: 38400 baud, 8N1, slave address 1
#define PFLOW_BAUD      38400
#define PFLOW_SLAVE_ID  1

// Flow rate spread across two consecutive holding registers
// Formula: (reg[0x003A] * 65536 + reg[0x003B]) / 1000.0  → SLPM
#define REG_FLOW_HI     0x003A
#define REG_FLOW_COUNT  2

ModbusMaster node;

void preTransmission()  { digitalWrite(RS485_DIR, HIGH); }
void postTransmission() { digitalWrite(RS485_DIR, LOW);  }

void setup()
{
    // PCF8575 powers on with all pins HIGH → all MOSFETs fire before firmware runs.
    // Write all-LOW immediately to suppress this before anything else.
    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.beginTransmission(PCF8575_ADDR);
    Wire.write(0x00);   // Port 0: all low
    Wire.write(0x00);   // Port 1: all low
    Wire.endTransmission();

    Serial.begin(115200);
    while (!Serial) {}
    Serial.println("PFLOW3008 RS485 test");

    pinMode(RS485_DIR, OUTPUT);
    digitalWrite(RS485_DIR, LOW);   // RX mode by default

    Serial2.begin(PFLOW_BAUD, SERIAL_8N1, RS485_RX, RS485_TX);

    node.begin(PFLOW_SLAVE_ID, Serial2);
    node.preTransmission(preTransmission);
    node.postTransmission(postTransmission);

    delay(600);  // sensor warm-up: 500 ms per datasheet + margin
}

void loop()
{
    uint8_t result = node.readHoldingRegisters(REG_FLOW_HI, REG_FLOW_COUNT);

    if (result == node.ku8MBSuccess) {
        uint32_t raw = ((uint32_t)node.getResponseBuffer(0) << 16)
                      | node.getResponseBuffer(1);
        float flow_slpm = raw / 1000.0f;
        Serial.printf("Flow: %.3f SLPM\n", flow_slpm);
    } else {
        Serial.printf("Modbus error: 0x%02X\n", result);
    }

    delay(500);
}
