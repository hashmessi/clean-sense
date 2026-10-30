// HOST STUB for ESP32Servo. Records the last commanded angle; no PWM is generated.
#pragma once
class Servo {
 public:
  void attach(int, int, int) {}
  void write(int deg) { last_ = deg; }
  int last_ = 90;
};
