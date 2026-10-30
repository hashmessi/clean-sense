// Host test for piezo_features.h. Synthetic waveforms only; this checks the arithmetic,
// not accuracy on real drops (that needs labelled data).
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <vector>
#include "piezo_features.h"

static int failures = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) {                                                      \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      failures++;                                                       \
    }                                                                   \
  } while (0)

static const size_t N = 256;

// one decaying burst starting at index `at`, carrier period `period` samples
static std::vector<int16_t> burst(size_t at, double amp, double tau, double period, size_t len = N) {
  std::vector<int16_t> x(len, 0);
  const double PI = 3.14159265358979323846;
  for (size_t i = at; i < len; i++) {
    double t = (double)(i - at);
    x[i] = (int16_t)std::lround(amp * std::exp(-t / tau) * std::sin(2 * PI * t / period));
  }
  return x;
}

static void test_silence_has_no_impact() {
  std::vector<int16_t> x(N, 0);
  piezo::Features f = piezo::extract(x.data(), N);
  CHECK(f.has_impact == 0);
  CHECK(f.peak == 0 && f.sub_impacts == 0 && f.decay_ms == 0);
}

static void test_noise_below_floor_is_no_impact() {
  std::vector<int16_t> x(N);
  for (size_t i = 0; i < N; i++) x[i] = (int16_t)((i % 3) - 1);  // +-1 count
  piezo::Features f = piezo::extract(x.data(), N);
  CHECK(f.has_impact == 0);
}

static void test_single_impact_one_burst_and_peak() {
  std::vector<int16_t> x = burst(40, 2000.0, 20.0, 12.0);
  piezo::Features f = piezo::extract(x.data(), N);
  CHECK(f.has_impact == 1);
  CHECK(f.peak > 1500 && f.peak <= 2000);
  CHECK(f.sub_impacts == 1);
  CHECK(f.decay_ms > 0 && f.decay_ms < 64);
}

static void test_two_separated_bursts_count_twice() {
  std::vector<int16_t> a = burst(20, 2000.0, 8.0, 12.0);
  std::vector<int16_t> b = burst(150, 1800.0, 8.0, 12.0);
  for (size_t i = 0; i < N; i++) a[i] = (int16_t)(a[i] + b[i]);
  piezo::Features f = piezo::extract(a.data(), N);
  CHECK(f.sub_impacts == 2);
}

static void test_long_ring_decays_slower_than_short_tap() {
  piezo::Features tap = piezo::extract(burst(30, 1500.0, 5.0, 10.0).data(), N);
  piezo::Features ring = piezo::extract(burst(30, 1500.0, 60.0, 10.0).data(), N);
  CHECK(ring.decay_ms > tap.decay_ms);
}

static void test_high_frequency_has_bigger_band_share() {
  piezo::Features hf = piezo::extract(burst(10, 1500.0, 200.0, 4.0).data(), N);
  piezo::Features lf = piezo::extract(burst(10, 1500.0, 200.0, 60.0).data(), N);
  CHECK(hf.band_permille > lf.band_permille);
}

static void test_zero_crossings_follow_frequency() {
  piezo::Features hf = piezo::extract(burst(0, 1500.0, 1000.0, 8.0).data(), N);
  piezo::Features lf = piezo::extract(burst(0, 1500.0, 1000.0, 40.0).data(), N);
  CHECK(hf.zero_crossings > lf.zero_crossings);
  CHECK(lf.zero_crossings >= 1);
}

static void test_no_overflow_at_full_scale() {
  std::vector<int16_t> x(N);
  for (size_t i = 0; i < N; i++) x[i] = (i % 2) ? INT16_MIN : INT16_MAX;
  piezo::Features f = piezo::extract(x.data(), N);
  CHECK(f.peak == 32768);
  CHECK(f.band_permille <= 1000000);
  CHECK(f.sub_impacts >= 1);
}

static void test_bad_input_is_safe() {
  piezo::Features f = piezo::extract(nullptr, 10);
  CHECK(f.has_impact == 0);
  int16_t one = 5000;
  f = piezo::extract(&one, 1);
  CHECK(f.has_impact == 0);
}

int main() {
  test_silence_has_no_impact();
  test_noise_below_floor_is_no_impact();
  test_single_impact_one_burst_and_peak();
  test_two_separated_bursts_count_twice();
  test_long_ring_decays_slower_than_short_tap();
  test_high_frequency_has_bigger_band_share();
  test_zero_crossings_follow_frequency();
  test_no_overflow_at_full_scale();
  test_bad_input_is_safe();
  if (failures) {
    std::fprintf(stderr, "%d piezo feature check(s) failed\n", failures);
    return 1;
  }
  std::printf("piezo features: all checks passed\n");
  return 0;
}
