// CleanSense ESP32 "B": piezo edge vote. ADVISORY ONLY, it never moves a motor and never
// decides "clean". It listens on GPIO 34 (ADC1_CH6, input only) through the bias network in
// INTEGRATION.md §3d, captures one 64 ms window after each impact, classifies it with the
// RULE BASELINE in piezo_rules.h, and prints one JSON line per impact.
//
// Serial protocol (115200 baud, JSON lines, laptop -> B):
//   vote        -> reply with the latest vote, or cls "none" conf 0.00 if none yet
// B -> laptop:
//   {"evt":"boot","mid":2048}                          once at start-up
//   {"evt":"edge_vote","cls":"hard|dry|none","conf":0.80,"peak":N,"sub":N,"decay_ms":N}
//
// Mid-rail is measured at boot over 256 quiet samples, so the bias network does not have
// to be exactly 1.65 V. Keep the piezo still during the first second after power-up.
#include "piezo_features.h"
#include "piezo_rules.h"

static const int PIEZO_ADC = 34;     // ESP32 B only: input-only pin, never drive it
static const int TRIGGER = 40;       // counts from mid-rail that start a capture (= NOISE_FLOOR)
static const int WINDOW = 256;       // piezo::SAMPLE_HZ * 64 ms
static const int COOLDOWN = 1000;    // samples (250 ms) after a capture before re-arming
static const unsigned long SAMPLE_US = 1000000UL / piezo::SAMPLE_HZ;

static int16_t window_buf[WINDOW];
static int mid = 2048;
static bool have_vote = false;
static piezo::Vote last_vote{"none", 0};
static piezo::Features last_feat{};

static void emit_vote(const piezo::Vote& v, const piezo::Features& f) {
  char buf[200];
  // conf printed from the integer percent so the text is identical on host and chip
  snprintf(buf, sizeof buf,
           "{\"evt\":\"edge_vote\",\"cls\":\"%s\",\"conf\":%d.%02d,\"peak\":%ld,\"sub\":%ld,\"decay_ms\":%ld}",
           v.cls, v.conf_pct / 100, v.conf_pct % 100,
           (long)f.peak, (long)f.sub_impacts, (long)f.decay_ms);
  Serial.print(buf);
  Serial.print('\n');
}

static void measure_mid() {
  long sum = 0;
  for (int i = 0; i < WINDOW; i++) {
    sum += analogRead(PIEZO_ADC);
    delayMicroseconds(SAMPLE_US);
  }
  mid = (int)(sum / WINDOW);
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  pinMode(PIEZO_ADC, INPUT);
  measure_mid();
  char buf[64];
  snprintf(buf, sizeof buf, "{\"evt\":\"boot\",\"mid\":%d}", mid);
  Serial.print(buf);
  Serial.print('\n');
}

static void handle_line(const String& s) {
  if (s.indexOf("vote") >= 0) {
    if (have_vote) emit_vote(last_vote, last_feat);
    else emit_vote(piezo::Vote{"none", 0}, piezo::Features{});
  }
}

void loop() {
  static unsigned long next_us = 0;
  static int cooldown = 0;
  static int capturing = -1;  // -1 idle, else index into window_buf

  while (Serial.available()) {
    String s = Serial.readStringUntil('\n');
    handle_line(s);
  }

  unsigned long now = micros();
  if ((long)(now - next_us) < 0) return;  // pace to SAMPLE_HZ
  next_us = now + SAMPLE_US;

  int x = analogRead(PIEZO_ADC) - mid;
  if (cooldown > 0) { cooldown--; return; }

  if (capturing < 0 && (x >= TRIGGER || x <= -TRIGGER)) capturing = 0;
  if (capturing >= 0) {
    window_buf[capturing++] = (int16_t)x;
    if (capturing == WINDOW) {
      last_feat = piezo::extract(window_buf, WINDOW);
      last_vote = piezo::classify(last_feat);
      have_vote = true;
      emit_vote(last_vote, last_feat);
      capturing = -1;
      cooldown = COOLDOWN;
    }
  }
}
