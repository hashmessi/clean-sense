// Host-side unit test for the firmware safety logic. Build and run:
//   g++ -std=c++17 -Wall -Wextra -Werror -I firmware/cleansense_a firmware/tests/test_limits.cpp -o /tmp/t && /tmp/t
#include <cassert>
#include <cstdio>
#include "limits.h"

using namespace limits;

int main() {
  // angle clamp
  assert(clampAngle(0) == SERVO_MIN_DEG);
  assert(clampAngle(200) == SERVO_MAX_DEG);
  assert(clampAngle(90) == 90);

  // geometry = CleanSense_Servo_Handoff (hardware-validated): home 0, sweep 0 -> 90 -> 0, never past 90
  assert(SERVO_HOME_DEG == 0 && SERVO_MIN_DEG == 0 && SERVO_MAX_DEG == 90);
  assert(clampAngle(-5) == 0 && clampAngle(91) == 90 && clampAngle(180) == 90);
  assert(reachDegrees(FULL) == 90);                       // the validated full sweep
  assert(reachDegrees(NARROW) == 40);                     // same 40 degree narrow span as before
  assert(SERVO_HOME_DEG + reachDegrees(FULL) <= SERVO_MAX_DEG);
  assert(SERVO_HOME_DEG + reachDegrees(NARROW) < SERVO_HOME_DEG + reachDegrees(FULL));

  // table: L1, L2, L3 must pass on a normal surface
  assert(motionAllowed(1, SLOW, NARROW, false, true));   // L1
  assert(motionAllowed(3, MED, FULL, false, true));      // L2
  assert(motionAllowed(1, SLOW, FULL, false, true));     // L3

  // sensitive surface: L1 and L3 yes, L2 no
  assert(motionAllowed(1, SLOW, NARROW, true, true));
  assert(motionAllowed(1, SLOW, FULL, true, true));      // L3
  assert(!motionAllowed(3, MED, FULL, true, true));      // L2 refused

  // out-of-table values refused
  assert(!motionAllowed(0, SLOW, NARROW, false, true));
  assert(!motionAllowed(4, SLOW, NARROW, false, true));
  assert(!motionAllowed(1, 7, NARROW, false, true));
  assert(!motionAllowed(1, SLOW, 9, false, true));

  // e-stop pressed refuses everything
  assert(!motionAllowed(1, SLOW, NARROW, false, false));

  // heartbeat: 3 s limit, wrap-safe
  assert(!heartbeatLost(2999, 0));
  assert(heartbeatLost(3001, 0));
  uint32_t nearWrap = 0xFFFFFF00u;
  assert(!heartbeatLost(nearWrap + 100, nearWrap));      // no false alarm across wrap
  assert(heartbeatLost(nearWrap + 4000, nearWrap));

  printf("firmware limits: all asserts passed\n");
  return 0;
}
