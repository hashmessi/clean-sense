// HOST SHIM for the Arduino core: enough to compile and run cleansense_a.ino on a PC.
// Simulated: pin levels (set from the test over stdin), the HX711 bit stream, millis,
// delay and the serial port (stdin/stdout). NOT simulated: electrical timing, PWM, brownout.
// Control lines on stdin start with '#' and are never seen by the sketch:
//   #pin <gpio> <0|1>   set an input level (21 e-stop, 22 cancel, 23 jumper)
//   #hx <counts>        next HX711 reading (24-bit two's complement)
#pragma once
#define ARDUINO 10800
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2

// PROGMEM helpers ArduinoJson expects when ARDUINO is defined (host: plain memory)
#define PROGMEM
#define pgm_read_byte(p) (*(const unsigned char*)(p))

class __FlashStringHelper;

class Print {
 public:
  virtual ~Print() {}
  virtual size_t write(uint8_t c) = 0;
  size_t write(const uint8_t* buf, size_t n) {
    size_t k = 0;
    for (; k < n; k++) write(buf[k]);
    return k;
  }
  size_t print(char c) { return write((uint8_t)c); }
  size_t print(const char* s) {
    size_t n = 0;
    for (; *s; s++, n++) write((uint8_t)*s);
    return n;
  }
};

class Printable {
 public:
  virtual ~Printable() {}
  virtual size_t printTo(Print& p) const = 0;
};

class Stream : public Print {
 public:
  virtual int available() = 0;
  virtual int read() = 0;
  size_t readBytes(char* buf, size_t n) {
    size_t k = 0;
    for (; k < n; k++) {
      int c = read();
      if (c < 0) break;
      buf[k] = (char)c;
    }
    return k;
  }
};

class String {
 public:
  bool concat(const char* s) { s_ += s; return true; }
  bool concat(char c) { s_ += c; return true; }
  bool concat(const String& o) { s_ += o.s_; return true; }
  String() {}
  String(const char* s) : s_(s ? s : "") {}
  String(const std::string& s) : s_(s) {}
  int length() const { return (int)s_.size(); }
  const char* c_str() const { return s_.c_str(); }
  int indexOf(const char* needle) const {
    size_t p = s_.find(needle);
    return p == std::string::npos ? -1 : (int)p;
  }
  String& trim() {
    const char* ws = " \t\r\n";
    size_t b = s_.find_first_not_of(ws);
    if (b == std::string::npos) { s_.clear(); return *this; }
    size_t e = s_.find_last_not_of(ws);
    s_ = s_.substr(b, e - b + 1);
    return *this;
  }
  std::string s_;
};

namespace shim {
inline std::mutex& mu() { static std::mutex m; return m; }
inline std::string& inbuf() { static std::string b; return b; }
inline std::map<int, int>& pins() { static std::map<int, int> p; return p; }
inline int& hx_value() { static int v = 0; return v; }
inline int& hx_pulse() { static int v = 0; return v; }
inline int& hx_dt() { static int v = 0; return v; }
inline int& sck_last() { static int v = 0; return v; }
// ADC simulation for ESP32 B: "#adcq v1 v2 ..." sets a cyclic list of samples. Empty => 2048.
inline std::vector<int>& adc_queue() { static std::vector<int> q; return q; }
inline size_t& adc_index() { static size_t i = 0; return i; }
inline std::chrono::steady_clock::time_point& t0() {
  static auto t = std::chrono::steady_clock::now(); return t;
}
}  // namespace shim

class SerialStub : public Stream {
 public:
  void begin(unsigned long) {}
  int read() { return -1; }
  int available() {
    std::lock_guard<std::mutex> g(shim::mu());
    return (int)shim::inbuf().size();
  }
  String readStringUntil(char term) {
    std::lock_guard<std::mutex> g(shim::mu());
    std::string& b = shim::inbuf();
    size_t p = b.find(term);
    std::string out = p == std::string::npos ? b : b.substr(0, p);
    b.erase(0, p == std::string::npos ? b.size() : p + 1);
    return String(out);
  }
  size_t write(uint8_t c) override {
    std::fputc(c, stdout);
    if (c == '\n') std::fflush(stdout);
    return 1;
  }
};
static SerialStub Serial;


inline uint32_t millis() {
  using namespace std::chrono;
  return (uint32_t)duration_cast<milliseconds>(steady_clock::now() - shim::t0()).count();
}
inline void delay(unsigned long ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }
inline void delayMicroseconds(unsigned int) {}
inline void pinMode(int, int) {}
inline void analogReadResolution(int) {}
inline int analogRead(int) {
  std::lock_guard<std::mutex> g(shim::mu());
  auto& q = shim::adc_queue();
  if (q.empty()) return 2048;
  int v = q[shim::adc_index() % q.size()];
  shim::adc_index()++;
  return v;
}
inline uint32_t micros() {
  using namespace std::chrono;
  return (uint32_t)duration_cast<microseconds>(steady_clock::now() - shim::t0()).count();
}

inline int digitalRead(int pin) {
  std::lock_guard<std::mutex> g(shim::mu());
  if (pin == 16) return shim::hx_dt();  // HX711 DT
  auto it = shim::pins().find(pin);
  return it == shim::pins().end() ? HIGH : it->second;  // pull-ups idle HIGH
}

inline void digitalWrite(int pin, int level) {
  if (pin != 4) return;  // HX711 SCK
  std::lock_guard<std::mutex> g(shim::mu());
  if (level == HIGH && shim::sck_last() == 0) {  // rising edge: one HX711 pulse
    shim::hx_pulse()++;
    int k = shim::hx_pulse();
    if (k <= 24) shim::hx_dt() = (shim::hx_value() >> (24 - k)) & 1;
    else shim::hx_dt() = 0;
  } else if (level == LOW && shim::sck_last() == 1) {  // falling edge
    if (shim::hx_pulse() >= 25) { shim::hx_pulse() = 0; shim::hx_dt() = 0; }
  }
  shim::sck_last() = level == HIGH ? 1 : 0;
}
