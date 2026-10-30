// HOST STUB for ESP32Servo. Records the commanded angles (last, lowest, highest); no PWM is generated.
#pragma once
class Servo {
 public:
  void attach(int, int, int) {}
  void write(int deg) {
    last_ = deg;
    if (deg < lo_) lo_ = deg;
    if (deg > hi_) hi_ = deg;
  }
  void reset_range() { lo_ = hi_ = last_; }
  int last_ = 0;
  int lo_ = 1000, hi_ = -1000;
};
