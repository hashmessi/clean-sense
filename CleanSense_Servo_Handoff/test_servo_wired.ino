/**
 * ============================================================================
 * Project:      CleanSense AI - Wired SG90 / High-Torque Servo Test Bench
 * Target Board: ESP32 DevKit (ESP-WROOM-32)
 * Environment:  PlatformIO / Arduino Framework (USB Serial @ 115200 Baud)
 * ============================================================================
 *
 * HARDWARE CONNECTIONS:
 * - Servo Signal (Yellow/Orange) = GPIO13 (labeled 'D13' on DevKit)
 * - Servo Power (Red, +5V to +6V) = External 5-6V Supply (3A+ rated) through Switch
 * - Servo Ground (Brown/Black)    = External Supply Ground (-)
 * - ESP32 Ground (GND)            = Common Ground connected to External Supply (-)
 *
 * CRITICAL ELECTRICAL & SAFETY NOTES:
 * - The firmware CANNOT add torque. Torque comes from the servo model and a
 *   power supply that delivers sufficient current (3A or more for high-torque servos).
 * - DO NOT power the servo from the ESP32 3V3 or 5V VIN pin! Stall currents will
 *   brown out the ESP32 or permanently damage onboard voltage regulators.
 * - Always power the servo from its own supply with a COMMON GROUND tied to the ESP32.
 * - Add a 1000 uF or larger low-ESR electrolytic capacitor across the servo power lines
 *   (Red +5V to Brown/Black GND) close to the servo to buffer peak transient spikes.
 * - If the ESP32 resets when the servo begins moving, the supply voltage is sagging.
 *   Fix the supply and wiring gauge before raising the motion speed.
 *
 * SERIAL COMMANDS (115200 Baud, newline-terminated):
 *   home               : Move smoothly to home position (0 deg)
 *   a <deg>            : Move smoothly to target angle (e.g. 'a 45')
 *   jump <deg>         : Fast mode: immediate direct angle write (no ramp)
 *                        *WARN: Draws sudden motor current spike!
 *   sweep              : Smooth pass: Home -> Sweep End -> Home
 *   multi <n>          : Multi-pass sweep (1 to 5 passes)
 *   profile <name>     : Switch servo profile: sg90, mg996r, ds3218, custom
 *   mode <preset>      : Set motion preset: gentle, normal, power
 *   speed <dps>        : Set motion speed in deg/s (30 to profile max dps)
 *   sweepspeed <dps>   : Set dedicated sweep speed in deg/s
 *   ease on|off        : Toggle acceleration/deceleration ramp (Smoothstep)
 *   hold on|off        : Continuous hold torque vs auto-detach after settling
 *   torque hold <ms>   : Configurable hold duration in ms before auto-detach
 *   range <min> <max>  : Adjust PWM pulse range in microseconds (400 to 2600 us)
 *   end <deg>          : Set sweep end angle (safe profile range)
 *   stop               : Emergency stop: halts motion immediately and detaches
 *   s                  : Show current angle, profile, mode, timers, and full status
 *   h                  : Print command help menu
 * ============================================================================
 */

#include <Arduino.h>
#include <ESP32Servo.h>
#include <math.h>

// ============================================================================
// CONFIGURABLE PIN & HARDWARE CONSTANTS
// ============================================================================
#define PIN_SERVO_SIG             13    // GPIO13 (labeled 'D13')
#define SERVO_FREQ_HZ             50    // 50 Hz PWM frequency (20ms standard period)

// Absolute Safety Calibration Envelopes
#define HARD_MIN_PULSE_LIMIT      400   // Absolute lower limit for calibration (us)
#define HARD_MAX_PULSE_LIMIT      2600  // Absolute upper limit for calibration (us)
#define ABSOLUTE_MIN_ANGLE        0     // Absolute mechanical minimum (deg)
#define ABSOLUTE_MAX_ANGLE        180   // Absolute mechanical maximum (deg)

// Motion Engine Intervals
#define SERVO_UPDATE_INTERVAL_MS  20    // 50 Hz-aligned position update interval (ms)
#define DEFAULT_SETTLE_MS         300   // Minimum post-arrival settling hold before detach

// Protection Limits
#define MAX_CONTINUOUS_MOTION_MS  60000 // 60 seconds continuous motion duty-cycle limit
#define COOLDOWN_DURATION_MS      15000 // 15 seconds enforced thermal cool-down
#define STALL_GUARD_WINDOW_MS     10000 // 10 seconds rolling window for sweep rate limit
#define STALL_GUARD_MAX_SWEEPS    3     // Max sweeps allowed within window before refusing power mode

// ============================================================================
// SERVO PROFILE TABLE (Datasheet Specifications)
// ============================================================================
struct ServoProfile {
  const char* id;
  const char* name;
  const char* datasheetRef;
  int minPulseUs;
  int maxPulseUs;
  float defaultSpeedDps;
  float maxSpeedDps;
  int minAngle;
  int maxAngle;
  float gentleDps;
  float normalDps;
  float powerDps;
};

// Profiles:
// 1. TowerPro SG90 (Micro-Servo):
//    - TowerPro Datasheet: 0.12 s/60° @ 4.8V (500 deg/s), ~0.10 s/60° @ 6.0V (600 deg/s)
//    - Pulse envelope: 500 us to 2400 us (standard micro-servo)
// 2. TowerPro MG996R (High-Torque Metal Gear):
//    - TowerPro Datasheet: 0.20 s/60° @ 4.8V (300 deg/s), 0.17 s/60° @ 6.0V (353 deg/s)
//    - Pulse envelope: 500 us to 2500 us
// 3. DSSERVO DS3218 (20kg Metal Gear Digital):
//    - DSSERVO Datasheet: 0.16 s/60° @ 6.0V (375 deg/s), 0.14 s/60° @ 6.8V (428 deg/s)
//    - Pulse envelope: 500 us to 2500 us
// 4. Custom Profile (Fully User Configurable)
const ServoProfile PROFILES[] = {
  {
    "sg90",
    "TowerPro SG90 (9g Micro-Servo)",
    "TowerPro Official Datasheet (0.12s/60deg @ 4.8V, 0.10s/60deg @ 6V)",
    500, 2400,
    250.0f, 500.0f,
    0, 180,
    90.0f, 250.0f, 500.0f
  },
  {
    "mg996r",
    "TowerPro MG996R (High-Torque Metal Gear)",
    "TowerPro Official Datasheet (0.20s/60deg @ 4.8V, 0.17s/60deg @ 6V)",
    500, 2500,
    200.0f, 350.0f,
    0, 180,
    80.0f, 200.0f, 350.0f
  },
  {
    "ds3218",
    "DSSERVO DS3218 (20kg Metal Gear Digital)",
    "DSSERVO Official Datasheet (0.16s/60deg @ 6V, 0.14s/60deg @ 6.8V)",
    500, 2500,
    220.0f, 400.0f,
    0, 180,
    90.0f, 220.0f, 400.0f
  },
  {
    "custom",
    "User Configured Profile",
    "User Defined Parameters",
    500, 2400,
    180.0f, 600.0f,
    0, 180,
    90.0f, 180.0f, 500.0f
  }
};

const int NUM_PROFILES = sizeof(PROFILES) / sizeof(PROFILES[0]);
int currentProfileIndex = 0; // Default: SG90 (Hardware currently installed)

// ============================================================================
// OPERATING MODES
// ============================================================================
enum MotionMode {
  MODE_GENTLE,  // Slow, high-torque positioning (cold/viscous spills)
  MODE_NORMAL,  // Balanced everyday responsive motion
  MODE_POWER,   // Maximum safe profile speed with acceleration ramp
  MODE_CUSTOM   // User manually overridden speed via 'speed' or 'sweepspeed'
};

MotionMode currentMode = MODE_NORMAL;

// ============================================================================
// SYSTEM MOTION STATES
// ============================================================================
enum MotionState {
  STATE_IDLE,
  STATE_MOVING_TO_ANGLE,
  STATE_SWEEP_OUTWARD,
  STATE_SWEEP_INWARD
};

// ============================================================================
// GLOBAL OBJECTS & RUNTIME CONFIGURATION
// ============================================================================
Servo wiperServo;

// Profile-derived Settings
int servoMinPulseUs       = PROFILES[0].minPulseUs;
int servoMaxPulseUs       = PROFILES[0].maxPulseUs;
int safeMinAngle          = PROFILES[0].minAngle;
int safeMaxAngle          = PROFILES[0].maxAngle;

// Motion Configuration
int homeAngle             = 0;
int sweepEndAngle         = 90;
float speedDps            = PROFILES[0].normalDps;
float sweepSpeedDps       = PROFILES[0].normalDps;
bool easeEnabled          = true;        // Smoothstep acceleration & deceleration ramp
bool holdEnabled          = false;       // Permanent hold (never detach)
unsigned long holdDurationMs = 0;        // Timed hold duration after arrival (0 = use settleTimeMs)
unsigned long settleTimeMs = DEFAULT_SETTLE_MS;

// Dynamic Motion Tracking
float currentAngle        = 0.0f;
float motionStartAngle    = 0.0f;
int targetAngle           = 0;
float activeSpeedDps      = PROFILES[0].normalDps;
MotionState currentState  = STATE_IDLE;

int multiPassesRemaining  = 0;
bool isMultiSweep         = false;

// Non-blocking Timers
unsigned long motionStartTime    = 0;
unsigned long motionDurationMs   = 0;
unsigned long lastServoUpdateMs  = 0;
unsigned long arrivalTime        = 0;
bool pendingDetach               = false;
int lastWrittenAngle             = -1;

// Protection & Thermal Tracking
unsigned long continuousMotionMs = 0;
unsigned long lastMotionTickMs   = 0;
bool inCooldown                  = false;
unsigned long cooldownExpiryMs   = 0;

// Stall-Risk Guard: Circular array for sweep request timestamps
unsigned long recentSweepTimes[8] = {0};
size_t sweepTimeHead             = 0;

// Command Line Parsing Buffer
char rxBuffer[64];
size_t rxIndex = 0;

// Forward Declarations
void printHelp();
void printStatus();
void processCommand(const char* cmd);
void handleSerialInput();
void updateMotion();
void attachServoIfNeeded();
void startMoveTo(int deg, float dps = 0.0f);
void jumpTo(int deg);
void startSweep(int passes = 1, float dps = 0.0f);
void stopMotion();
int clampAngle(int angle);
void applyProfile(int profileIdx);
void setMode(MotionMode mode);
bool checkStallGuard();
void recordSweepTime();

// ============================================================================
// ARDUINO SETUP
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(300);

  Serial.println();
  Serial.println("==========================================================================");
  Serial.println("   CleanSense AI: ESP32 Servo Motion Firmware (High-Torque / SG90)");
  Serial.println("   Target: ESP32 DevKit (WROOM-32) | USB Serial @ 115200 Baud");
  Serial.println("==========================================================================");
  Serial.printf("[INIT] Active Profile:      %s (%s)\n",
                PROFILES[currentProfileIndex].name, PROFILES[currentProfileIndex].id);
  Serial.printf("[INIT] Servo Signal Pin:    GPIO%d (labeled 'D13')\n", PIN_SERVO_SIG);
  Serial.printf("[INIT] Safe Angle Range:    %d deg to %d deg\n", safeMinAngle, safeMaxAngle);
  Serial.printf("[INIT] PWM Pulse Range:     %d us - %d us (50 Hz PWM)\n", servoMinPulseUs, servoMaxPulseUs);
  Serial.printf("[INIT] Motion Mode:         NORMAL (Speed: %.0f deg/s | Sweepspeed: %.0f deg/s)\n",
                speedDps, sweepSpeedDps);
  Serial.printf("[INIT] Motion Ramp:         Smoothstep Easing %s (50 Hz non-blocking update)\n",
                (easeEnabled ? "ENABLED" : "DISABLED"));
  Serial.println("[INIT] Power Safety Guard:  Duty-Cycle limit: 60s max continuous | 15s cooldown");
  Serial.println("[INIT] Stall-Risk Guard:    Refuses >3 full-speed sweeps in 10s window");
  Serial.println("--------------------------------------------------------------------------");
  Serial.println("[SAFETY NOTE] The servo is NOT energized on boot (0 movement on boot).");
  Serial.println("              Ensure external 5-6V supply is switched ON.");
  Serial.println("              Begin testing with small angles (e.g. 'a 10').");
  Serial.println("==========================================================================");

  // Pre-allocate hardware timers for ESP32 PWM
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  wiperServo.setPeriodHertz(SERVO_FREQ_HZ);

  // Initial state: strictly detached on boot
  currentState = STATE_IDLE;
  pendingDetach = false;
  lastWrittenAngle = -1;

  printHelp();
  Serial.printf("\n[STATUS] State: IDLE | Profile: %s | Servo: DETACHED | Angle: %d deg\n",
                PROFILES[currentProfileIndex].id, (int)round(currentAngle));
  Serial.print("CleanSense-Servo> ");
}

// ============================================================================
// MAIN LOOP (Strictly Non-Blocking)
// ============================================================================
void loop() {
  // 1. Process incoming ASCII commands from Serial
  handleSerialInput();

  // 2. Drive 50 Hz non-blocking servo motion engine & protection timers
  updateMotion();
}

// ============================================================================
// PROFILE MANAGEMENT
// ============================================================================
void applyProfile(int profileIdx) {
  if (profileIdx < 0 || profileIdx >= NUM_PROFILES) return;
  currentProfileIndex = profileIdx;

  const ServoProfile& prof = PROFILES[currentProfileIndex];
  servoMinPulseUs = prof.minPulseUs;
  servoMaxPulseUs = prof.maxPulseUs;
  safeMinAngle    = prof.minAngle;
  safeMaxAngle    = prof.maxAngle;

  // Apply default profile speed
  speedDps        = prof.normalDps;
  sweepSpeedDps   = prof.normalDps;
  currentMode     = MODE_NORMAL;

  // If currently attached, re-attach with new profile pulse limits
  if (wiperServo.attached()) {
    wiperServo.detach();
    attachServoIfNeeded();
  }

  Serial.println("\n--------------------------------------------------------------------------");
  Serial.printf("[PROFILE] Switched to '%s': %s\n", prof.id, prof.name);
  Serial.printf("          Datasheet Spec:  %s\n", prof.datasheetRef);
  Serial.printf("          Pulse Envelope:  %d us to %d us (50 Hz)\n", servoMinPulseUs, servoMaxPulseUs);
  Serial.printf("          Speed Envelope:  Gentle: %.0f | Normal: %.0f | Power: %.0f deg/s\n",
                prof.gentleDps, prof.normalDps, prof.powerDps);
  Serial.printf("          Safe Travel:     [%d deg, %d deg]\n", safeMinAngle, safeMaxAngle);
  Serial.println("--------------------------------------------------------------------------");
  Serial.print("CleanSense-Servo> ");
}

// ============================================================================
// OPERATING MODE SELECTION (Gentle / Normal / Power)
// ============================================================================
void setMode(MotionMode mode) {
  const ServoProfile& prof = PROFILES[currentProfileIndex];

  switch (mode) {
    case MODE_GENTLE:
      currentMode = MODE_GENTLE;
      speedDps = prof.gentleDps;
      sweepSpeedDps = prof.gentleDps;
      easeEnabled = true;
      Serial.printf("[MODE] Set to GENTLE (Speed: %.0f deg/s, Ramp: ON).\n", speedDps);
      Serial.println("       Optimized for high load, thick or cold spills.");
      break;

    case MODE_NORMAL:
      currentMode = MODE_NORMAL;
      speedDps = prof.normalDps;
      sweepSpeedDps = prof.normalDps;
      easeEnabled = true;
      Serial.printf("[MODE] Set to NORMAL (Speed: %.0f deg/s, Ramp: ON).\n", speedDps);
      Serial.println("       Balanced standard operation.");
      break;

    case MODE_POWER:
      // Check stall-risk guard before enabling power mode
      if (!checkStallGuard()) {
        Serial.println("[STALL GUARD] REJECTED 'mode power' request!");
        Serial.printf("              More than %d sweeps attempted within the last %d seconds.\n",
                      STALL_GUARD_MAX_SWEEPS, STALL_GUARD_WINDOW_MS / 1000);
        Serial.println("              Staying in current mode to prevent supply collapse & servo overheating.");
        Serial.print("CleanSense-Servo> ");
        return;
      }
      currentMode = MODE_POWER;
      speedDps = prof.powerDps;
      sweepSpeedDps = prof.powerDps;
      easeEnabled = true; // Ramp is strictly preserved to prevent sudden current spikes!
      Serial.printf("[MODE] Set to POWER (Speed: %.0f deg/s, Ramp: STRICTLY ON).\n", speedDps);
      Serial.println("       Max profile velocity. Easing ramp is active to buffer current spikes.");
      break;

    case MODE_CUSTOM:
      currentMode = MODE_CUSTOM;
      break;
  }
  Serial.print("CleanSense-Servo> ");
}

// ============================================================================
// STALL-RISK GUARD: RATE-LIMITING FULL SPEED SWEEPS
// ============================================================================
bool checkStallGuard() {
  unsigned long now = millis();
  int recentCount = 0;
  size_t totalSlots = sizeof(recentSweepTimes) / sizeof(recentSweepTimes[0]);

  for (size_t i = 0; i < totalSlots; i++) {
    if (recentSweepTimes[i] > 0 && (now - recentSweepTimes[i] <= STALL_GUARD_WINDOW_MS)) {
      recentCount++;
    }
  }
  return (recentCount < STALL_GUARD_MAX_SWEEPS);
}

void recordSweepTime() {
  recentSweepTimes[sweepTimeHead] = millis();
  sweepTimeHead = (sweepTimeHead + 1) % (sizeof(recentSweepTimes) / sizeof(recentSweepTimes[0]));
}

// ============================================================================
// SERVO ATTACH HELPER (Jitter-Free First-Pulse Alignment)
// ============================================================================
void attachServoIfNeeded() {
  if (!wiperServo.attached()) {
    wiperServo.setPeriodHertz(SERVO_FREQ_HZ);
    wiperServo.attach(PIN_SERVO_SIG, servoMinPulseUs, servoMaxPulseUs);

    // Write current known position immediately on attachment to prevent jump
    int initialAngle = clampAngle((int)round(currentAngle));
    wiperServo.write(initialAngle);
    lastWrittenAngle = initialAngle;
  }
}

// ============================================================================
// MOTION ENGINE (50 Hz Aligned, Smoothstep Ramp, Duty-Cycle Protection)
// ============================================================================
void updateMotion() {
  unsigned long now = millis();

  // Handle thermal cool-down recovery
  if (inCooldown) {
    if (now >= cooldownExpiryMs) {
      inCooldown = false;
      continuousMotionMs = 0;
      Serial.println("\n[PROTECTION] Enforced thermal cool-down complete. Servo unlocked.");
      Serial.print("CleanSense-Servo> ");
    }
    return;
  }

  // 1. Active motion interpolation
  if (currentState != STATE_IDLE) {
    // Duty-Cycle accumulation
    if (lastMotionTickMs > 0) {
      continuousMotionMs += (now - lastMotionTickMs);
    }
    lastMotionTickMs = now;

    // Enforce 60s continuous motion limit
    if (continuousMotionMs >= MAX_CONTINUOUS_MOTION_MS) {
      stopMotion();
      inCooldown = true;
      cooldownExpiryMs = now + COOLDOWN_DURATION_MS;
      Serial.println("\n**************************************************************************");
      Serial.printf("[PROTECTION] DUTY-CYCLE LIMIT REACHED (%lu seconds continuous motion)!\n",
                    (unsigned long)(MAX_CONTINUOUS_MOTION_MS / 1000));
      Serial.printf("             Enforcing %lu second cool-down to prevent servo & supply overheat.\n",
                    (unsigned long)(COOLDOWN_DURATION_MS / 1000));
      Serial.println("             Servo DETACHED. All movement commands locked.");
      Serial.println("**************************************************************************");
      Serial.print("CleanSense-Servo> ");
      return;
    }

    if (now - lastServoUpdateMs >= SERVO_UPDATE_INTERVAL_MS) {
      lastServoUpdateMs = now;

      attachServoIfNeeded();

      unsigned long elapsed = now - motionStartTime;
      float t = (motionDurationMs > 0) ? ((float)elapsed / (float)motionDurationMs) : 1.0f;
      if (t >= 1.0f) t = 1.0f;

      // Acceleration and Deceleration Ramp
      float p = t;
      if (easeEnabled) {
        // Smoothstep S-curve: 3t^2 - 2t^3 (zero derivative at t=0 and t=1)
        p = t * t * (3.0f - 2.0f * t);
      }

      // Interpolate angle
      currentAngle = motionStartAngle + ((float)targetAngle - motionStartAngle) * p;
      currentAngle = (float)clampAngle((int)round(currentAngle));

      int commandedAngle = (int)round(currentAngle);

      // JITTER REDUCTION: Skip redundant writes
      if (commandedAngle != lastWrittenAngle) {
        wiperServo.write(commandedAngle);
        lastWrittenAngle = commandedAngle;
      }

      // Progress reporting
      static int lastLoggedAngle = -999;
      if (abs(commandedAngle - lastLoggedAngle) >= 15 || t >= 1.0f) {
        Serial.printf("[MOVING] Current: %3d deg -> Target: %3d deg (Progress: %3.0f%%)\n",
                      commandedAngle, targetAngle, t * 100.0f);
        lastLoggedAngle = commandedAngle;
      }

      // Check arrival at current target
      if (t >= 1.0f) {
        currentAngle = (float)targetAngle;
        if (targetAngle != lastWrittenAngle) {
          wiperServo.write(targetAngle);
          lastWrittenAngle = targetAngle;
        }

        if (currentState == STATE_MOVING_TO_ANGLE) {
          Serial.printf("[ARRIVED] Reached %d deg successfully.\n", targetAngle);
          currentState = STATE_IDLE;
          lastMotionTickMs = 0;
          arrivalTime = millis();
          pendingDetach = !holdEnabled;
          Serial.print("CleanSense-Servo> ");
        }
        else if (currentState == STATE_SWEEP_OUTWARD) {
          Serial.printf("[SWEEP] Reached sweep end (%d deg). Reversing toward home (%d deg)...\n",
                        targetAngle, homeAngle);
          motionStartAngle = (float)targetAngle;
          targetAngle = homeAngle;
          float deltaDeg = fabs((float)targetAngle - motionStartAngle);
          motionStartTime = millis();
          motionDurationMs = (unsigned long)round((deltaDeg / activeSpeedDps) * 1000.0f);
          if (motionDurationMs < SERVO_UPDATE_INTERVAL_MS) motionDurationMs = SERVO_UPDATE_INTERVAL_MS;
          currentState = STATE_SWEEP_INWARD;
        }
        else if (currentState == STATE_SWEEP_INWARD) {
          if (isMultiSweep && multiPassesRemaining > 1) {
            multiPassesRemaining--;
            Serial.printf("[SWEEP] Completed pass. Passes remaining: %d. Starting next pass...\n", multiPassesRemaining);
            motionStartAngle = (float)targetAngle;
            targetAngle = sweepEndAngle;
            float deltaDeg = fabs((float)targetAngle - motionStartAngle);
            motionStartTime = millis();
            motionDurationMs = (unsigned long)round((deltaDeg / activeSpeedDps) * 1000.0f);
            if (motionDurationMs < SERVO_UPDATE_INTERVAL_MS) motionDurationMs = SERVO_UPDATE_INTERVAL_MS;
            currentState = STATE_SWEEP_OUTWARD;
          } else {
            Serial.printf("[SWEEP] Finished all passes at home position (%d deg).\n", targetAngle);
            currentState = STATE_IDLE;
            lastMotionTickMs = 0;
            isMultiSweep = false;
            multiPassesRemaining = 0;
            arrivalTime = millis();
            pendingDetach = !holdEnabled;
            Serial.print("CleanSense-Servo> ");
          }
        }
      }
    }
  } else {
    // When idle, reset continuous motion accumulator after 2 seconds of rest
    if (continuousMotionMs > 0 && (now - arrivalTime > 2000)) {
      continuousMotionMs = 0;
    }
  }

  // 2. Post-arrival settling and auto-detachment (eliminates holding jitter, heat, & buzzing)
  if (pendingDetach && !holdEnabled && currentState == STATE_IDLE) {
    unsigned long requiredHoldMs = (holdDurationMs > 0) ? holdDurationMs : settleTimeMs;
    if (now - arrivalTime >= requiredHoldMs) {
      if (wiperServo.attached()) {
        wiperServo.detach();
        Serial.printf("[SERVO] Detached. Holding period (%lu ms) elapsed at %d deg (IDLE).\n",
                      requiredHoldMs, (int)round(currentAngle));
        Serial.print("CleanSense-Servo> ");
      }
      pendingDetach = false;
    }
  }
}

// ============================================================================
// MOTION COMMAND HELPERS
// ============================================================================
void startMoveTo(int deg, float dps) {
  if (inCooldown) {
    long rem = (long)((cooldownExpiryMs - millis()) / 1000);
    if (rem < 0) rem = 0;
    Serial.printf("[PROTECTION] In cooldown! Remaining: %ld s. Motion refused.\n", rem);
    Serial.print("CleanSense-Servo> ");
    return;
  }

  deg = clampAngle(deg);
  targetAngle = deg;
  motionStartAngle = currentAngle;

  const ServoProfile& prof = PROFILES[currentProfileIndex];
  if (dps >= 30.0f && dps <= prof.maxSpeedDps) {
    activeSpeedDps = dps;
  } else {
    activeSpeedDps = speedDps;
  }

  float deltaDeg = fabs((float)targetAngle - motionStartAngle);
  if (deltaDeg < 0.01f) {
    if (!wiperServo.attached() && holdEnabled) {
      attachServoIfNeeded();
    }
    Serial.printf("[SERVO] Already at %d deg.\n", targetAngle);
    Serial.print("CleanSense-Servo> ");
    return;
  }

  motionStartTime = millis();
  motionDurationMs = (unsigned long)round((deltaDeg / activeSpeedDps) * 1000.0f);
  if (motionDurationMs < SERVO_UPDATE_INTERVAL_MS) {
    motionDurationMs = SERVO_UPDATE_INTERVAL_MS;
  }

  attachServoIfNeeded();
  currentState = STATE_MOVING_TO_ANGLE;
  pendingDetach = false;
  lastMotionTickMs = millis();

  Serial.printf("[CMD:MOVE] Moving from %.0f deg to %d deg (Speed: %.0f deg/s, Ramp: %s, Duration: %lu ms)...\n",
                motionStartAngle, targetAngle, activeSpeedDps, (easeEnabled ? "ON" : "OFF"), motionDurationMs);
}

void jumpTo(int deg) {
  if (inCooldown) {
    long rem = (long)((cooldownExpiryMs - millis()) / 1000);
    if (rem < 0) rem = 0;
    Serial.printf("[PROTECTION] In cooldown! Remaining: %ld s. Jump refused.\n", rem);
    Serial.print("CleanSense-Servo> ");
    return;
  }

  deg = clampAngle(deg);
  Serial.println("**************************************************************************");
  Serial.println("[WARN:JUMP] Fast jump mode: immediate position command with no acceleration ramp.");
  Serial.println("            WARNING: Draws peak motor current spike! Requires 3A+ rated supply.");
  Serial.println("**************************************************************************");

  targetAngle = deg;
  motionStartAngle = (float)deg;
  currentAngle = (float)deg;

  attachServoIfNeeded();
  wiperServo.write(deg);
  lastWrittenAngle = deg;

  currentState = STATE_IDLE;
  arrivalTime = millis();
  pendingDetach = !holdEnabled;

  Serial.printf("[ARRIVED:JUMP] Directly commanded to %d deg (Servo: %s).\n",
                deg, (holdEnabled ? "HOLDING ATTACHED" : "SETTLING"));
  Serial.print("CleanSense-Servo> ");
}

void startSweep(int passes, float dps) {
  if (inCooldown) {
    long rem = (long)((cooldownExpiryMs - millis()) / 1000);
    if (rem < 0) rem = 0;
    Serial.printf("[PROTECTION] In cooldown! Remaining: %ld s. Sweep refused.\n", rem);
    Serial.print("CleanSense-Servo> ");
    return;
  }

  // Stall Guard Rate Limiter
  if (!checkStallGuard()) {
    Serial.println("[STALL GUARD] Rapid sweep limit reached (>3 sweeps within 10s)!");
    Serial.println("              Sweep throttled to protect power supply and motor coils.");
    Serial.print("CleanSense-Servo> ");
    return;
  }
  recordSweepTime();

  if (passes < 1) passes = 1;
  if (passes > 5) passes = 5;

  multiPassesRemaining = passes;
  isMultiSweep = (passes > 1);

  const ServoProfile& prof = PROFILES[currentProfileIndex];
  if (dps >= 30.0f && dps <= prof.maxSpeedDps) {
    activeSpeedDps = dps;
  } else {
    activeSpeedDps = sweepSpeedDps;
  }

  targetAngle = sweepEndAngle;
  motionStartAngle = currentAngle;
  float deltaDeg = fabs((float)targetAngle - motionStartAngle);
  motionStartTime = millis();
  motionDurationMs = (unsigned long)round((deltaDeg / activeSpeedDps) * 1000.0f);
  if (motionDurationMs < SERVO_UPDATE_INTERVAL_MS) motionDurationMs = SERVO_UPDATE_INTERVAL_MS;

  attachServoIfNeeded();
  currentState = STATE_SWEEP_OUTWARD;
  pendingDetach = false;
  lastMotionTickMs = millis();

  Serial.printf("[CMD:SWEEP] Starting %d pass(es): %.0f deg -> %d deg -> %d deg (Speed: %.0f deg/s, Ramp: %s)...\n",
                passes, currentAngle, sweepEndAngle, homeAngle, activeSpeedDps, (easeEnabled ? "ON" : "OFF"));
}

void stopMotion() {
  currentState = STATE_IDLE;
  targetAngle = (int)round(currentAngle);
  motionStartAngle = currentAngle;
  isMultiSweep = false;
  multiPassesRemaining = 0;
  pendingDetach = false;
  lastMotionTickMs = 0;

  if (wiperServo.attached()) {
    wiperServo.detach();
  }

  Serial.printf("\n[EMERGENCY STOP] Motion HALTED immediately. Servo DETACHED at %d deg.\n", targetAngle);
  Serial.print("CleanSense-Servo> ");
}

int clampAngle(int angle) {
  if (angle < safeMinAngle) return safeMinAngle;
  if (angle > safeMaxAngle) return safeMaxAngle;
  return angle;
}

// ============================================================================
// SERIAL COMMAND PARSER (Line-buffered, Non-blocking)
// ============================================================================
void handleSerialInput() {
  static bool lastWasCR = false;
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\n' && lastWasCR) {
      lastWasCR = false;
      continue;
    }
    lastWasCR = (c == '\r');

    // Echo character back for interactive typing in terminal
    Serial.print(c);

    if (c == '\n' || c == '\r') {
      Serial.println(); // Move to new line
      if (rxIndex > 0) {
        rxBuffer[rxIndex] = '\0';
        processCommand(rxBuffer);
        rxIndex = 0;
      } else {
        Serial.print("CleanSense-Servo> ");
      }
    } else {
      if (rxIndex < sizeof(rxBuffer) - 1) {
        rxBuffer[rxIndex++] = c;
      } else {
        rxIndex = 0; // Buffer overflow protection
      }
    }
  }
}

void processCommand(const char* rawCmd) {
  // Trim leading spaces
  while (*rawCmd == ' ' || *rawCmd == '\t') rawCmd++;
  if (*rawCmd == '\0') {
    Serial.print("CleanSense-Servo> ");
    return;
  }

  char cmd[64];
  strncpy(cmd, rawCmd, sizeof(cmd) - 1);
  cmd[sizeof(cmd) - 1] = '\0';

  // Trim trailing spaces
  size_t len = strlen(cmd);
  while (len > 0 && (cmd[len - 1] == ' ' || cmd[len - 1] == '\t')) {
    cmd[--len] = '\0';
  }

  // 1. HOME
  if (strcasecmp(cmd, "home") == 0) {
    startMoveTo(homeAngle);
    return;
  }

  // 2. FAST JUMP: 'jump <deg>'
  if (strncasecmp(cmd, "jump", 4) == 0) {
    const char* arg = cmd + 4;
    while (*arg == ' ' || *arg == '\t') arg++;

    if (*arg == '\0') {
      Serial.printf("[ERROR] Missing angle! Usage: jump <deg> (Range: %d to %d deg)\n", safeMinAngle, safeMaxAngle);
      Serial.print("CleanSense-Servo> ");
      return;
    }

    char* endPtr = nullptr;
    long val = strtol(arg, &endPtr, 10);
    if (endPtr == arg) {
      Serial.printf("[ERROR] Invalid angle '%s'! Must be between %d and %d.\n", arg, safeMinAngle, safeMaxAngle);
      Serial.print("CleanSense-Servo> ");
      return;
    }

    jumpTo((int)val);
    return;
  }

  // 3. MOVE TO ANGLE: 'a <deg>'
  if (strncasecmp(cmd, "a ", 2) == 0 || strcasecmp(cmd, "a") == 0 ||
      ((cmd[0] == 'a' || cmd[0] == 'A') && (isdigit((unsigned char)cmd[1]) || cmd[1] == '-'))) {
    const char* arg = cmd + 1;
    while (*arg == ' ' || *arg == '\t') arg++;

    if (*arg == '\0') {
      Serial.printf("[ERROR] Missing angle! Usage: a <deg> (e.g. 'a 45' or 'a 90', Range: %d to %d deg)\n",
                    safeMinAngle, safeMaxAngle);
      Serial.print("CleanSense-Servo> ");
      return;
    }

    char* endPtr = nullptr;
    long val = strtol(arg, &endPtr, 10);
    if (endPtr == arg) {
      Serial.printf("[ERROR] Invalid angle '%s'! Must be between %d and %d.\n", arg, safeMinAngle, safeMaxAngle);
      Serial.print("CleanSense-Servo> ");
      return;
    }

    int requestedAngle = (int)val;
    if (requestedAngle < safeMinAngle || requestedAngle > safeMaxAngle) {
      Serial.printf("[WARN] Angle %d outside safe profile limits! Clamping to [%d, %d] deg.\n",
                    requestedAngle, safeMinAngle, safeMaxAngle);
    }
    startMoveTo(requestedAngle);
    return;
  }

  // 4. SINGLE SWEEP: 'sweep'
  if (strcasecmp(cmd, "sweep") == 0) {
    startSweep(1);
    return;
  }

  // 5. MULTI SWEEP: 'multi <n>'
  if (strncasecmp(cmd, "multi", 5) == 0) {
    const char* arg = cmd + 5;
    while (*arg == ' ' || *arg == '\t') arg++;

    int passes = 3;
    if (*arg != '\0') {
      char* endPtr = nullptr;
      long val = strtol(arg, &endPtr, 10);
      if (endPtr == arg) {
        Serial.println("[ERROR] Multi-pass count must be a number between 1 and 5 (e.g. 'multi 2').");
        Serial.print("CleanSense-Servo> ");
        return;
      }
      passes = (int)val;
    }

    if (passes < 1 || passes > 5) {
      Serial.println("[WARN] Multi-pass count must be between 1 and 5. Clamping.");
      if (passes < 1) passes = 1;
      if (passes > 5) passes = 5;
    }

    startSweep(passes);
    return;
  }

  // 6. SERVO PROFILES: 'profile <name>'
  if (strncasecmp(cmd, "profile", 7) == 0) {
    const char* arg = cmd + 7;
    while (*arg == ' ' || *arg == '\t') arg++;

    if (*arg == '\0') {
      Serial.printf("[PROFILE] Active profile: '%s' (%s)\n",
                    PROFILES[currentProfileIndex].id, PROFILES[currentProfileIndex].name);
      Serial.println("          Available profiles: sg90 | mg996r | ds3218 | custom");
      Serial.println("          Usage: profile <name> (e.g. 'profile sg90' or 'profile mg996r')");
      Serial.print("CleanSense-Servo> ");
      return;
    }

    for (int i = 0; i < NUM_PROFILES; i++) {
      if (strcasecmp(arg, PROFILES[i].id) == 0) {
        applyProfile(i);
        return;
      }
    }

    Serial.printf("[ERROR] Unknown profile '%s'! Available profiles: sg90, mg996r, ds3218, custom\n", arg);
    Serial.print("CleanSense-Servo> ");
    return;
  }

  // 7. MOTION MODES: 'mode <gentle|normal|power>'
  if (strncasecmp(cmd, "mode", 4) == 0) {
    const char* arg = cmd + 4;
    while (*arg == ' ' || *arg == '\t') arg++;

    if (*arg == '\0') {
      const char* mStr = (currentMode == MODE_GENTLE) ? "GENTLE" :
                         (currentMode == MODE_NORMAL) ? "NORMAL" :
                         (currentMode == MODE_POWER)  ? "POWER" : "CUSTOM";
      Serial.printf("[MODE] Current mode: %s (Speed: %.0f deg/s)\n", mStr, speedDps);
      Serial.println("       Usage: mode gentle | mode normal | mode power");
      Serial.print("CleanSense-Servo> ");
      return;
    }

    if (strcasecmp(arg, "gentle") == 0) {
      setMode(MODE_GENTLE);
      return;
    } else if (strcasecmp(arg, "normal") == 0) {
      setMode(MODE_NORMAL);
      return;
    } else if (strcasecmp(arg, "power") == 0) {
      setMode(MODE_POWER);
      return;
    } else {
      Serial.printf("[ERROR] Unknown mode '%s'! Choose: gentle, normal, power\n", arg);
      Serial.print("CleanSense-Servo> ");
      return;
    }
  }

  // 8. TORQUE HOLD / HOLD: 'torque hold <ms>' or 'hold on|off|<ms>'
  if (strncasecmp(cmd, "torque hold", 11) == 0 || strncasecmp(cmd, "hold", 4) == 0) {
    const char* arg = (strncasecmp(cmd, "torque hold", 11) == 0) ? (cmd + 11) : (cmd + 4);
    while (*arg == ' ' || *arg == '\t') arg++;

    if (*arg == '\0') {
      Serial.printf("[HOLD] Current holding setting: %s (Timed hold: %lu ms, Settle: %lu ms)\n",
                    (holdEnabled ? "ON (Permanent)" : (holdDurationMs > 0 ? "TIMED" : "OFF (Auto-detach)")),
                    holdDurationMs, settleTimeMs);
      Serial.println("       Usage: hold on | hold off | hold <ms> | torque hold <ms>");
      Serial.print("CleanSense-Servo> ");
      return;
    }

    if (strcasecmp(arg, "on") == 0 || strcasecmp(arg, "1") == 0) {
      holdEnabled = true;
      holdDurationMs = 0;
      pendingDetach = false;
      attachServoIfNeeded();
      Serial.println("[HOLD] Continuous holding torque ENABLED (Servo remains permanently energized).");
      Serial.print("CleanSense-Servo> ");
      return;
    } else if (strcasecmp(arg, "off") == 0 || strcasecmp(arg, "0") == 0) {
      holdEnabled = false;
      holdDurationMs = 0;
      pendingDetach = true;
      arrivalTime = millis();
      Serial.printf("[HOLD] Auto-detach ENABLED (Servo detaches %lu ms after arrival to stop buzzing).\n", settleTimeMs);
      Serial.print("CleanSense-Servo> ");
      return;
    } else {
      char* endPtr = nullptr;
      long ms = strtol(arg, &endPtr, 10);
      if (endPtr != arg && ms > 0) {
        holdEnabled = false;
        holdDurationMs = (unsigned long)ms;
        Serial.printf("[HOLD] Timed torque hold set: Servo will hold position for %lu ms after arrival, then detach.\n",
                      holdDurationMs);
        Serial.print("CleanSense-Servo> ");
        return;
      }
      Serial.printf("[ERROR] Invalid hold parameter '%s'! Usage: hold on | hold off | hold <ms>\n", arg);
      Serial.print("CleanSense-Servo> ");
      return;
    }
  }

  // 9. SWEEPSPEED: 'sweepspeed <dps>'
  if (strncasecmp(cmd, "sweepspeed", 10) == 0) {
    const char* arg = cmd + 10;
    while (*arg == ' ' || *arg == '\t') arg++;

    const ServoProfile& prof = PROFILES[currentProfileIndex];
    if (*arg == '\0') {
      Serial.printf("[SWEEPSPEED] Current sweep speed: %.0f deg/s (Profile max: %.0f deg/s).\n",
                    sweepSpeedDps, prof.maxSpeedDps);
      Serial.printf("             Usage: sweepspeed <dps> (Range: 30 to %.0f deg/s)\n", prof.maxSpeedDps);
      Serial.print("CleanSense-Servo> ");
      return;
    }

    char* endPtr = nullptr;
    float val = strtof(arg, &endPtr);
    if (endPtr == arg || val < 30.0f || val > prof.maxSpeedDps) {
      Serial.printf("[ERROR] Sweep speed must be between 30 and %.0f deg/s for %s.\n",
                    prof.maxSpeedDps, prof.id);
      Serial.print("CleanSense-Servo> ");
      return;
    }

    sweepSpeedDps = val;
    currentMode = MODE_CUSTOM;
    Serial.printf("[SWEEPSPEED] Sweep speed updated to %.0f deg/s.\n", sweepSpeedDps);
    Serial.print("CleanSense-Servo> ");
    return;
  }

  // 10. SPEED: 'speed <dps>'
  if (strncasecmp(cmd, "speed", 5) == 0) {
    const char* arg = cmd + 5;
    while (*arg == ' ' || *arg == '\t') arg++;

    const ServoProfile& prof = PROFILES[currentProfileIndex];
    if (*arg == '\0') {
      Serial.printf("[SPEED] Current motion speed: %.0f deg/s (Sweepspeed: %.0f deg/s, Profile max: %.0f deg/s).\n",
                    speedDps, sweepSpeedDps, prof.maxSpeedDps);
      Serial.printf("        Usage: speed <dps> (Range: 30 to %.0f deg/s)\n", prof.maxSpeedDps);
      Serial.print("CleanSense-Servo> ");
      return;
    }

    char* endPtr = nullptr;
    float val = strtof(arg, &endPtr);
    if (endPtr == arg || val < 30.0f || val > prof.maxSpeedDps) {
      Serial.printf("[ERROR] Speed must be between 30 and %.0f deg/s for %s.\n",
                    prof.maxSpeedDps, prof.id);
      Serial.print("CleanSense-Servo> ");
      return;
    }

    speedDps = val;
    currentMode = MODE_CUSTOM;
    Serial.printf("[SPEED] Motion speed updated to %.0f deg/s.\n", speedDps);
    Serial.print("CleanSense-Servo> ");
    return;
  }

  // 11. EASING: 'ease on|off'
  if (strncasecmp(cmd, "ease", 4) == 0) {
    const char* arg = cmd + 4;
    while (*arg == ' ' || *arg == '\t') arg++;

    if (strcasecmp(arg, "on") == 0 || strcasecmp(arg, "1") == 0) {
      easeEnabled = true;
      Serial.println("[EASE] Smoothstep acceleration/deceleration ramp ENABLED.");
      Serial.print("CleanSense-Servo> ");
      return;
    } else if (strcasecmp(arg, "off") == 0 || strcasecmp(arg, "0") == 0) {
      easeEnabled = false;
      Serial.println("[EASE] Acceleration/deceleration ramp DISABLED (Constant velocity).");
      Serial.print("CleanSense-Servo> ");
      return;
    } else {
      Serial.printf("[EASE] Easing ramp is currently %s. Usage: ease on | ease off\n",
                    (easeEnabled ? "ON" : "OFF"));
      Serial.print("CleanSense-Servo> ");
      return;
    }
  }

  // 12. PULSE RANGE: 'range <min_us> <max_us>'
  if (strncasecmp(cmd, "range", 5) == 0) {
    const char* arg = cmd + 5;
    while (*arg == ' ' || *arg == '\t') arg++;

    if (*arg == '\0') {
      Serial.printf("[RANGE] Current PWM pulse range: %d us to %d us (50 Hz).\n",
                    servoMinPulseUs, servoMaxPulseUs);
      Serial.printf("        Usage: range <min_us> <max_us> (Limits: %d to %d us)\n",
                    HARD_MIN_PULSE_LIMIT, HARD_MAX_PULSE_LIMIT);
      Serial.println("        WARNING: Setting limits beyond mechanical stops will cause servo buzzing,");
      Serial.println("                 stall overheating, and excessive power supply current draw!");
      Serial.print("CleanSense-Servo> ");
      return;
    }

    char* endPtr = nullptr;
    long minUs = strtol(arg, &endPtr, 10);
    if (endPtr == arg) {
      Serial.println("[ERROR] Invalid range syntax! Usage: range <min_us> <max_us>");
      Serial.print("CleanSense-Servo> ");
      return;
    }

    while (*endPtr == ' ' || *endPtr == '\t') endPtr++;
    if (*endPtr == '\0') {
      Serial.println("[ERROR] Missing max_us! Usage: range <min_us> <max_us>");
      Serial.print("CleanSense-Servo> ");
      return;
    }

    char* endPtr2 = nullptr;
    long maxUs = strtol(endPtr, &endPtr2, 10);
    if (endPtr2 == endPtr || minUs < HARD_MIN_PULSE_LIMIT || maxUs > HARD_MAX_PULSE_LIMIT || minUs >= maxUs) {
      Serial.printf("[ERROR] Pulse limits must satisfy %d <= min_us < max_us <= %d us.\n",
                    HARD_MIN_PULSE_LIMIT, HARD_MAX_PULSE_LIMIT);
      Serial.print("CleanSense-Servo> ");
      return;
    }

    servoMinPulseUs = (int)minUs;
    servoMaxPulseUs = (int)maxUs;

    if (wiperServo.attached()) {
      wiperServo.detach();
      attachServoIfNeeded();
    }

    Serial.printf("[RANGE] Updated servo pulse range to [%d us, %d us].\n",
                  servoMinPulseUs, servoMaxPulseUs);
    Serial.println("        WARNING: Verify horn rotation carefully to avoid mechanical binding.");
    Serial.print("CleanSense-Servo> ");
    return;
  }

  // 13. SWEEP END ANGLE: 'end <deg>'
  if (strncasecmp(cmd, "end", 3) == 0) {
    const char* arg = cmd + 3;
    while (*arg == ' ' || *arg == '\t') arg++;

    if (*arg == '\0') {
      Serial.printf("[END] Current sweep end angle: %d deg.\n", sweepEndAngle);
      Serial.printf("      Usage: end <deg> (Safe range: %d to %d deg)\n", safeMinAngle, safeMaxAngle);
      Serial.print("CleanSense-Servo> ");
      return;
    }

    char* endPtr = nullptr;
    long val = strtol(arg, &endPtr, 10);
    if (endPtr == arg) {
      Serial.printf("[ERROR] Invalid end angle '%s'! Must be between %d and %d.\n",
                    arg, safeMinAngle, safeMaxAngle);
      Serial.print("CleanSense-Servo> ");
      return;
    }

    int deg = clampAngle((int)val);
    sweepEndAngle = deg;

    Serial.printf("[END] Sweep end angle updated to %d deg.\n", sweepEndAngle);
    Serial.print("CleanSense-Servo> ");
    return;
  }

  // 14. STOP: Emergency Halt
  if (strcasecmp(cmd, "stop") == 0) {
    stopMotion();
    return;
  }

  // 15. STATUS: 's'
  if (strcasecmp(cmd, "s") == 0) {
    printStatus();
    Serial.print("CleanSense-Servo> ");
    return;
  }

  // 16. HELP: 'h'
  if (strcasecmp(cmd, "h") == 0 || strcasecmp(cmd, "?") == 0) {
    printHelp();
    Serial.print("CleanSense-Servo> ");
    return;
  }

  // Unknown command
  Serial.printf("[ERROR] Unknown command '%s'. Send 'h' for help.\n", cmd);
  Serial.print("CleanSense-Servo> ");
}

void printStatus() {
  const ServoProfile& prof = PROFILES[currentProfileIndex];
  const char* mStr = (currentMode == MODE_GENTLE) ? "GENTLE" :
                     (currentMode == MODE_NORMAL) ? "NORMAL" :
                     (currentMode == MODE_POWER)  ? "POWER" : "CUSTOM";

  Serial.println("\n----------------- [SERVO SYSTEM STATUS] -----------------");
  Serial.printf("  Active Profile:      %s (%s)\n", prof.id, prof.name);
  Serial.printf("  Datasheet Spec:      %s\n", prof.datasheetRef);
  Serial.printf("  Operating Mode:      %s\n", mStr);
  Serial.printf("  Signal Pin:          GPIO%d (D13)\n", PIN_SERVO_SIG);
  Serial.printf("  Current Angle:       %d deg\n", (int)round(currentAngle));
  Serial.printf("  Target Angle:        %d deg\n", targetAngle);
  Serial.printf("  Motion State:        %s\n", (currentState == STATE_IDLE ? "IDLE" : "MOVING"));
  Serial.printf("  Servo Attached:      %s\n", (wiperServo.attached() ? "YES" : "NO (Detached)"));
  Serial.printf("  Holding Torque:      %s\n",
                (holdEnabled ? "ON (Permanent)" : (holdDurationMs > 0 ? "TIMED (Holding against load)" : "OFF (Auto-detach on idle)")));
  if (holdDurationMs > 0 && !holdEnabled) {
    Serial.printf("  Hold Duration:       %lu ms\n", holdDurationMs);
  }
  Serial.printf("  Settle Time:         %lu ms\n", settleTimeMs);
  Serial.printf("  Motion Speed:        %.0f deg/s (Profile Max: %.0f deg/s)\n", speedDps, prof.maxSpeedDps);
  Serial.printf("  Sweep Speed:         %.0f deg/s\n", sweepSpeedDps);
  Serial.printf("  Easing (Ramp):       %s (Smoothstep)\n", (easeEnabled ? "ENABLED" : "DISABLED"));
  Serial.printf("  PWM Pulse Range:     [%d us, %d us] (50 Hz / 20ms period)\n", servoMinPulseUs, servoMaxPulseUs);
  Serial.printf("  Safe Travel Limits:  [%d deg, %d deg]\n", safeMinAngle, safeMaxAngle);
  Serial.printf("  Home Angle:          %d deg\n", homeAngle);
  Serial.printf("  Sweep End Angle:     %d deg\n", sweepEndAngle);
  Serial.printf("  Continuous Motion:   %lu / %lu ms\n", continuousMotionMs, (unsigned long)MAX_CONTINUOUS_MOTION_MS);
  Serial.printf("  Thermal Cooldown:    %s\n", (inCooldown ? "ACTIVE (Servo Locked)" : "READY (Normal)"));
  Serial.println("---------------------------------------------------------");
}

void printHelp() {
  Serial.println("\n==========================================================================");
  Serial.println("                           COMMAND MENU");
  Serial.println("==========================================================================");
  Serial.println("  home              : Move smoothly to home position (0 deg)");
  Serial.println("  a <deg>           : Move smoothly to target angle (e.g. 'a 45')");
  Serial.println("  jump <deg>        : Fast mode: immediate direct angle write (no ramp)");
  Serial.println("                      *WARN: Peak current spike! Ensure supply is 3A+ rated.");
  Serial.println("  sweep             : Smooth pass (Home -> Sweep End -> Home)");
  Serial.println("  multi <n>         : Multi-pass sweep (1-5 passes)");
  Serial.println("  profile <name>    : Switch servo profile: sg90 | mg996r | ds3218 | custom");
  Serial.println("  mode <preset>     : Set preset: gentle | normal | power (with ramp)");
  Serial.println("  speed <dps>       : Set motion speed in deg/s (30 to profile max)");
  Serial.println("  sweepspeed <dps>  : Set dedicated sweep speed in deg/s");
  Serial.println("  ease on|off       : Toggle acceleration/deceleration ramp (Smoothstep)");
  Serial.println("  hold on|off       : Toggle permanent holding torque vs auto-detach");
  Serial.println("  torque hold <ms>  : Set timed holding torque (ms) before auto-detach");
  Serial.println("  range <min> <max> : Set servo pulse envelope in us (400 to 2600 us)");
  Serial.println("  end <deg>         : Set sweep end angle (safe profile range)");
  Serial.println("  stop              : Emergency stop: halts motion and detaches");
  Serial.println("  s                 : Show current status, profile, mode, and settings");
  Serial.println("  h                 : Print this help menu");
  Serial.println("==========================================================================");
  Serial.println("SAFETY & HARDWARE INSTRUCTIONS:");
  Serial.println("  - Firmware CANNOT add torque. Torque comes from servo & power supply.");
  Serial.println("  - Power servo from a SEPARATE 5-6V supply (3A+) with ESP32 common ground.");
  Serial.println("  - Add a 1000 uF electrolytic capacitor across the servo power lines.");
  Serial.println("  - If ESP32 resets on motion, the supply is sagging. Fix before speeding up!");
  Serial.println("==========================================================================");
}
