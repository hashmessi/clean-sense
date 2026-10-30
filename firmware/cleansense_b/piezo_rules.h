// CleanSense ESP32 "B": RULE BASELINE on the piezo features. No trained model, no measured
// accuracy. Every threshold below is an uncalibrated placeholder and is named so it can be
// replaced by measured values later. ADVISORY ONLY: the laptop fuses the vote, and fusion
// can only make a run more careful (see edge.py).
//
// Rules, first match wins:
//   no impact in the window                          -> none  0.00
//   peak >= HARD_PEAK and decay <= HARD_DECAY_MS     -> hard  0.80 (safety-biased: a sharp,
//                                                        high impact routes to human only)
//   sub_impacts >= DRY_MIN_BURSTS                    -> dry   0.50 (below MIN_CONF 0.70, so it
//                                                        cannot choose a plan by itself)
//   any other impact                                 -> none  0.30
// LIQUID IS NOT DETECTED. Nothing in the piezo features separates a liquid spill from a
// quiet window, so this baseline never reports "liquid". The laptop uses the operator's
// answer for liquid.
#pragma once
#include <stdint.h>

#include "piezo_features.h"

namespace piezo {

constexpr int32_t HARD_PEAK = 2000;      // counts above mid-rail (placeholder, uncalibrated)
constexpr int32_t HARD_DECAY_MS = 20;    // a sharp strike dies away within this (placeholder)
constexpr int32_t DRY_MIN_BURSTS = 2;    // separate bursts that suggest granular debris (placeholder)

struct Vote {
  const char* cls;  // "hard" | "dry" | "none"
  int conf_pct;     // 0..100, integer so the host and the chip print the same text
};

inline Vote classify(const Features& f) {
  if (!f.has_impact) return {"none", 0};
  if (f.peak >= HARD_PEAK && f.decay_ms <= HARD_DECAY_MS) return {"hard", 80};
  if (f.sub_impacts >= DRY_MIN_BURSTS) return {"dry", 50};
  return {"none", 30};
}

}  // namespace piezo
