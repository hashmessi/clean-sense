// CleanSense ESP32 "A": sweeper + load-cell streamer. JSON lines over USB serial.
// Libraries (Arduino Library Manager): ESP32Servo, ArduinoJson (v6).
// NOT YET COMPILED ON HARDWARE. Safety logic is host-tested in limits.h.
//
// Pins (from IMPLEMENTATION_TASKS.md; avoid GPIO 0/2/12/15):
//   HX711 DT 16, SCK 4 | servo PWM 18 | e-stop sense 21 (HIGH = healthy, button pulls LOW)
//   cancel 22 (LOW = pressed) | surface jumper 23 (LOW = sensitive)
#include <string.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include "limits.h"

constexpr int PIN_HX_DT = 16, PIN_HX_SCK = 4;
constexpr int PIN_SERVO = 18, PIN_ESTOP = 21, PIN_CANCEL = 22, PIN_SENSITIVE = 23;

// Set by calibration (known-mass test). 1.0 = raw counts; must be calibrated before use.
float CAL_FACTOR = 1.0f;
long hxOffset = 0;

Servo servo;
uint32_t lastPingMs = 0;
bool sensitive = false;
bool cancelWasPressed = false;
bool cancelRequested = false;  // set during motion; cleared at sweep start
int currentDeg = limits::SERVO_HOME_DEG;

// ---------- load cell (bit-banged HX711, gain 128) -------------------------
long hxRaw() {
  while (digitalRead(PIN_HX_DT) == HIGH) { delay(1); }  // wait for data ready
  long v = 0;
  for (int i = 0; i < 24; i++) {
    digitalWrite(PIN_HX_SCK, HIGH); delayMicroseconds(1);
    v = (v << 1) | digitalRead(PIN_HX_DT);
    digitalWrite(PIN_HX_SCK, LOW); delayMicroseconds(1);
  }
  digitalWrite(PIN_HX_SCK, HIGH); delayMicroseconds(1);  // gain 128 pulse
  digitalWrite(PIN_HX_SCK, LOW);  delayMicroseconds(1);
  if (v & 0x800000) v |= 0xFF000000;  // sign-extend 24-bit
  return v;
}

void tareScale() {
  long sum = 0;
  for (int i = 0; i < 10; i++) sum += hxRaw();
  hxOffset = sum / 10;
}

// ---------- helpers ---------------------------------------------------------
bool estopOk() { return digitalRead(PIN_ESTOP) == HIGH; }

void sendLine(const JsonDocument& doc) {
  serializeJson(doc, Serial);
  Serial.print('\n');
}

void parkHead() {
  currentDeg = limits::SERVO_HOME_DEG;
  servo.write(currentDeg);
}

void sendFault(const char* code) {
  StaticJsonDocument<96> d;
  d["evt"] = "fault";
  d["code"] = code;
  sendLine(d);
}

// While motion is running, keep reading the serial port: pings refresh the
// heartbeat (otherwise a long L2 sweep would look like laptop silence), and a
// cancel press stops the motion. Other commands are ignored until motion ends.
void pumpWhileBusy() {
  while (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    if (line.indexOf("\"ping\"") >= 0) lastPingMs = millis();
  }
  bool pressed = digitalRead(PIN_CANCEL) == LOW;
  if (pressed && !cancelWasPressed) cancelRequested = true;
  cancelWasPressed = pressed;
}

// Move the head one way, one degree per step, checking e-stop, heartbeat and cancel each step.
bool moveTo(int target, uint32_t stepMs) {
  target = limits::clampAngle(target);
  while (currentDeg != target) {
    pumpWhileBusy();
    if (!estopOk()) return false;
    if (cancelRequested) return false;
    if (limits::heartbeatLost(millis(), lastPingMs)) return false;
    currentDeg += (target > currentDeg) ? 1 : -1;
    servo.write(currentDeg);
    delay(stepMs);
  }
  return true;
}

void doSweep(int id, int passes, int speed, int amp) {
  cancelRequested = false;
  if (!limits::motionAllowed(passes, speed, amp, sensitive, estopOk())) {
    parkHead();
    sendFault(estopOk() ? "refused" : "estop");
    return;
  }
  int span = limits::ampDegrees(amp);
  uint32_t stepMs = limits::stepDelayMs(speed);
  int centre = limits::SERVO_HOME_DEG;
  for (int p = 0; p < passes; p++) {
    if (!moveTo(centre - span, stepMs) || !moveTo(centre + span, stepMs)) {
      parkHead();
      sendFault(!estopOk() ? "estop" : cancelRequested ? "cancelled"
                : limits::heartbeatLost(millis(), lastPingMs) ? "timeout" : "fault");
      return;
    }
    if (!moveTo(centre, stepMs)) {
      parkHead();
      sendFault(!estopOk() ? "estop" : cancelRequested ? "cancelled"
                : limits::heartbeatLost(millis(), lastPingMs) ? "timeout" : "fault");
      return;
    }
  }
  StaticJsonDocument<64> d;
  d["evt"] = "motion_done";
  d["id"] = id;
  sendLine(d);
}

// ---------- command handling ------------------------------------------------
void handleLine(const String& line) {
  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, line)) {
    sendFault("bad_json");
    return;
  }
  const char* cmd = doc["cmd"] | "";
  if (!strcmp(cmd, "ping")) {
    lastPingMs = millis();
  } else if (!strcmp(cmd, "park")) {
    parkHead();
    StaticJsonDocument<32> d; d["evt"] = "parked"; sendLine(d);
  } else if (!strcmp(cmd, "tare")) {
    tareScale();
    StaticJsonDocument<32> d; d["evt"] = "tared"; sendLine(d);
  } else if (!strcmp(cmd, "weigh")) {
    float grams = (hxRaw() - hxOffset) / CAL_FACTOR;
    StaticJsonDocument<192> d;  // 96 silently dropped "settled" (ArduinoJson adds nothing on overflow)
    d["evt"] = "mass"; d["g"] = grams; d["std"] = 0.0; d["settled"] = true;
    sendLine(d);
  } else if (!strcmp(cmd, "sweep")) {
    doSweep(doc["id"] | 0, doc["passes"] | 0,
            !strcmp(doc["speed"] | "", "med") ? limits::MED : limits::SLOW,
            !strcmp(doc["amp"] | "", "full") ? limits::FULL : limits::NARROW);
  } else {
    sendFault("unknown_cmd");
  }
}

// ---------- arduino entry points -------------------------------------------
void setup() {
  Serial.begin(115200);
  pinMode(PIN_HX_SCK, OUTPUT);
  pinMode(PIN_HX_DT, INPUT);
  pinMode(PIN_ESTOP, INPUT_PULLUP);
  pinMode(PIN_CANCEL, INPUT_PULLUP);
  pinMode(PIN_SENSITIVE, INPUT_PULLUP);
  servo.attach(PIN_SERVO, 500, 2400);
  parkHead();
  lastPingMs = millis();
}

void loop() {
  // laptop heartbeat: silence for 3 s => park
  if (limits::heartbeatLost(millis(), lastPingMs) && currentDeg != limits::SERVO_HOME_DEG) {
    parkHead();
  }
  // e-stop: cut and report once
  static bool wasStopped = false;
  if (!estopOk() && !wasStopped) {
    parkHead();
    StaticJsonDocument<32> d; d["evt"] = "estop"; d["state"] = true; sendLine(d);
  }
  wasStopped = !estopOk();
  // jumper: sensitive surface
  sensitive = (digitalRead(PIN_SENSITIVE) == LOW);
  // cancel button: send one event per press
  bool pressed = digitalRead(PIN_CANCEL) == LOW;
  if (pressed && !cancelWasPressed) {
    StaticJsonDocument<32> d; d["evt"] = "cancel"; sendLine(d);
  }
  cancelWasPressed = pressed;
  // commands from laptop
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length()) handleLine(line);
  }
}
