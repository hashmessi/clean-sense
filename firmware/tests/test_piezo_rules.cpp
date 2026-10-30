// Host test for the ESP32 B rule baseline (piezo_rules.h). Thresholds are placeholders, so
// these tests pin the BOUNDARIES of the current rules, not measured accuracy.
#include <cassert>
#include <initializer_list>
#include <cstdio>
#include <cstring>

#include "piezo_rules.h"

using namespace piezo;

static int failures = 0;
#define CHECK(cond)                                                    \
  do {                                                                 \
    if (!(cond)) {                                                     \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      failures++;                                                      \
    }                                                                  \
  } while (0)

static Features feat(int32_t peak, int32_t decay_ms, int32_t sub, int32_t has = 1) {
  Features f{};
  f.peak = peak;
  f.decay_ms = decay_ms;
  f.sub_impacts = sub;
  f.has_impact = has;
  return f;
}

int main() {
  // no impact: none at zero confidence, whatever the other fields say
  {
    Vote v = classify(feat(0, 0, 0, 0));
    CHECK(std::strcmp(v.cls, "none") == 0 && v.conf_pct == 0);
  }
  // hard: at the peak limit with a fast decay
  {
    Vote v = classify(feat(HARD_PEAK, HARD_DECAY_MS, 1));
    CHECK(std::strcmp(v.cls, "hard") == 0 && v.conf_pct == 80);
  }
  // one count under the peak limit: not hard
  {
    Vote v = classify(feat(HARD_PEAK - 1, HARD_DECAY_MS, 1));
    CHECK(std::strcmp(v.cls, "none") == 0 && v.conf_pct == 30);
  }
  // high peak but slow decay: not hard (a long ring is not a sharp strike)
  {
    Vote v = classify(feat(HARD_PEAK, HARD_DECAY_MS + 1, 1));
    CHECK(std::strcmp(v.cls, "none") == 0);
  }
  // two separate bursts below the hard peak: dry, and the confidence stays under MIN_CONF 0.70
  {
    Vote v = classify(feat(800, 10, DRY_MIN_BURSTS));
    CHECK(std::strcmp(v.cls, "dry") == 0 && v.conf_pct == 50);
    CHECK(v.conf_pct < 70);  // the rule baseline can never change a plan by itself
  }
  // one burst only: not dry
  {
    Vote v = classify(feat(800, 10, DRY_MIN_BURSTS - 1));
    CHECK(std::strcmp(v.cls, "none") == 0 && v.conf_pct == 30);
  }
  // the rules never emit liquid: every output class is one of hard, dry, none
  for (int peak : {0, 40, 800, HARD_PEAK, 4000}) {
    for (int sub : {0, 1, 2, 5}) {
      Vote v = classify(feat(peak, 5, sub, peak >= 40 ? 1 : 0));
      CHECK(std::strcmp(v.cls, "liquid") != 0);
      CHECK(std::strcmp(v.cls, "hard") == 0 || std::strcmp(v.cls, "dry") == 0 ||
            std::strcmp(v.cls, "none") == 0);
    }
  }
  if (failures) {
    std::fprintf(stderr, "%d piezo rule check(s) failed\n", failures);
    return 1;
  }
  std::printf("piezo rules: all checks passed\n");
  return 0;
}
