// Safety limits for the ESP32 sweeper. Pure logic: no Arduino types, so it is
// unit-tested on the laptop with g++ (see firmware/tests/test_limits.cpp).
#pragma once
#include <stdint.h>

namespace limits {

// Geometry from CleanSense_Servo_Handoff (hardware-validated): parked at 0 degrees, a pass sweeps 0 -> 90 -> 0.
// Nothing may command past 90.
constexpr int SERVO_MIN_DEG = 0;
constexpr int SERVO_MAX_DEG = 90;
constexpr int SERVO_HOME_DEG = 0;
constexpr int MAX_PASSES = 3;
constexpr uint32_t HEARTBEAT_TIMEOUT_MS = 3000;

enum Speed { SLOW = 0, MED = 1 };
enum Amp { NARROW = 0, FULL = 1 };

inline int clampAngle(int a) {
  if (a < SERVO_MIN_DEG) return SERVO_MIN_DEG;
  if (a > SERVO_MAX_DEG) return SERVO_MAX_DEG;
  return a;
}

// Motion gate. Refuses anything outside the table, any motion with e-stop
// pressed, and on a sensitive surface any multi-pass or medium-speed motion
// (that is L2, which the laptop must never ask for on a sensitive surface).
inline bool motionAllowed(int passes, int speed, int amp, bool sensitive, bool estopOk) {
  if (!estopOk) return false;
  if (passes < 1 || passes > MAX_PASSES) return false;
  if (speed != SLOW && speed != MED) return false;
  if (amp != NARROW && amp != FULL) return false;
  if (sensitive && (passes > 1 || speed == MED)) return false;
  return true;
}

// How far one pass goes from home. FULL is the validated 0 -> 90 sweep; NARROW keeps the earlier 40 degree span.
inline int reachDegrees(int amp) { return amp == FULL ? 90 : 40; }
static_assert(SERVO_HOME_DEG >= SERVO_MIN_DEG && SERVO_HOME_DEG + 90 <= SERVO_MAX_DEG, "full sweep must fit");
inline uint32_t stepDelayMs(int speed) { return speed == SLOW ? 25 : 10; }

// Unsigned subtraction handles millis() wrap-around.
inline bool heartbeatLost(uint32_t nowMs, uint32_t lastPingMs) {
  return (uint32_t)(nowMs - lastPingMs) > HEARTBEAT_TIMEOUT_MS;
}

}  // namespace limits
