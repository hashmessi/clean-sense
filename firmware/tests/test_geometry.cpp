// Host test: the REAL sketch, driven through the shim. A sweep must follow the hardware-validated geometry of
// CleanSense_Servo_Handoff: parked at 0 degrees, out to 90 at most, back to 0, never below 0 or above 90.
#include <Arduino.h>
#include "cleansense_a.ino"

#include <cassert>
#include <cstdio>

static void send(const char* line) {
  std::lock_guard<std::mutex> g(shim::mu());
  shim::inbuf() += line;
  shim::inbuf() += "\n";
}

int main() {
  setup();
  assert(servo.last_ == 0);                    // parked at the validated home
  servo.reset_range();

  send("{\"cmd\":\"sweep\",\"id\":1,\"passes\":1,\"speed\":\"med\",\"amp\":\"full\"}");
  loop();
  assert(servo.hi_ == 90 && servo.lo_ == 0 && servo.last_ == 0);  // L2/L3 style: the validated 0 -> 90 -> 0

  servo.reset_range();
  send("{\"cmd\":\"ping\"}");  // keep the 3 s heartbeat alive between the two sweeps
  loop();
  send("{\"cmd\":\"sweep\",\"id\":2,\"passes\":1,\"speed\":\"slow\",\"amp\":\"narrow\"}");
  loop();
  assert(servo.hi_ == 40 && servo.lo_ == 0 && servo.last_ == 0);  // L1: gentle, 40 degrees, home again

  servo.reset_range();
  send("{\"cmd\":\"park\"}");
  loop();
  assert(servo.last_ == 0 && servo.hi_ == 0);
  printf("servo geometry: all asserts passed\n");
  return 0;
}
