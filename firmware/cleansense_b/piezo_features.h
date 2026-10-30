// CleanSense ESP32 "B": impact features from one piezo window. Integer math only, so the
// same numbers come out on the host test and on the chip. Pure functions, no hardware access.
//
// Window: ADC1 samples at 4 kHz, centred on mid-rail before this call (raw ADC minus mid).
// Output feeds the edge vote. It is ADVISORY: the laptop fuses it, it never decides "clean".
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace piezo {

constexpr int SAMPLE_HZ = 4000;        // 256 samples = 64 ms window
constexpr int NOISE_FLOOR = 40;        // counts; below this the window holds no impact
constexpr int SUB_HIGH_PCT = 30;       // a sub-impact starts when |x| reaches 30% of the peak
constexpr int SUB_REARM_PCT = 15;      // ...and re-arms only after |x| falls back under 15%
constexpr int DECAY_PCT = 10;          // decay time: until the envelope drops under 10% of the peak
constexpr int ENV_HALF = 8;            // envelope = max |x| over +-8 samples (~4 ms), so the
                                       // carrier's zero crossings do not end a burst early

struct Features {
  int32_t peak;            // max |x| in counts
  int32_t decay_ms;        // time from peak until |x| < 10% of peak (window length if never)
  int32_t zero_crossings;  // sign changes in the window
  int32_t band_permille;   // energy of first difference / energy of x, x1000 (high-frequency share)
  int32_t sub_impacts;     // separate bursts above 30% of peak, with hysteresis
  int32_t has_impact;      // 1 when peak >= NOISE_FLOOR
};

inline int32_t absi(int32_t v) { return v < 0 ? -v : v; }

// envelope at sample i: max |x| in a centred window (computed on the fly, no buffer)
inline int32_t envelope(const int16_t* x, size_t n, size_t i) {
  size_t lo = i >= (size_t)ENV_HALF ? i - ENV_HALF : 0;
  size_t hi = i + ENV_HALF < n ? i + ENV_HALF : n - 1;
  int32_t m = 0;
  for (size_t j = lo; j <= hi; j++) {
    int32_t a = absi(x[j]);
    if (a > m) m = a;
  }
  return m;
}

inline Features extract(const int16_t* x, size_t n, int sample_hz = SAMPLE_HZ) {
  Features f = {0, 0, 0, 0, 0, 0};
  if (x == nullptr || n < 2) return f;

  // peak (raw) and its index
  int32_t peak = 0;
  size_t ip = 0;
  for (size_t i = 0; i < n; i++) {
    int32_t a = absi(x[i]);
    if (a > peak) { peak = a; ip = i; }
  }
  f.peak = peak;
  if (peak < NOISE_FLOOR) return f;  // no impact: other fields stay at their "nothing" values
  f.has_impact = 1;

  // decay: samples after the peak until the envelope is under DECAY_PCT of the peak
  size_t end = n;
  for (size_t i = ip + 1; i < n; i++) {
    if (envelope(x, n, i) * 100 < peak * DECAY_PCT) { end = i; break; }
  }
  f.decay_ms = (int32_t)(((int64_t)(end - ip)) * 1000 / sample_hz);

  // zero crossings
  int32_t zc = 0;
  for (size_t i = 1; i < n; i++) {
    if ((x[i - 1] < 0 && x[i] >= 0) || (x[i - 1] >= 0 && x[i] < 0)) zc++;
  }
  f.zero_crossings = zc;

  // band share: energy of the first difference over energy of the signal
  int64_t e_x = 0, e_d = 0;
  for (size_t i = 0; i < n; i++) e_x += (int64_t)x[i] * x[i];
  for (size_t i = 1; i < n; i++) {
    int64_t d = (int64_t)x[i] - x[i - 1];
    e_d += d * d;
  }
  if (e_x > 0) {
    int64_t share = e_d * 1000 / e_x;
    f.band_permille = (int32_t)(share > 1000000 ? 1000000 : share);
  }

  // sub-impacts with hysteresis on the envelope
  const int32_t hi = peak * SUB_HIGH_PCT / 100;
  const int32_t lo = peak * SUB_REARM_PCT / 100;
  bool armed = true;
  int32_t bursts = 0;
  for (size_t i = 0; i < n; i++) {
    int32_t a = envelope(x, n, i);
    if (armed && a >= hi) { bursts++; armed = false; }
    else if (!armed && a < lo) { armed = true; }
  }
  f.sub_impacts = bursts;
  return f;
}

}  // namespace piezo
