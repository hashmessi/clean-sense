// Host build of cleansense_b.ino: the real sketch, with the Arduino shim underneath.
// Extra harness control on stdin: "#adcq v1 v2 ... vN" sets the simulated ADC samples
// (cyclic). Empty queue reads a constant 2048 (mid-rail).
#include <Arduino.h>
#include "cleansense_b.ino"

#include <sstream>
#include <thread>

static void stdin_reader() {
  char line[4096];
  while (std::fgets(line, sizeof line, stdin)) {
    std::string s(line);
    if (!s.empty() && s[0] == '#') {
      if (s.rfind("#adcq", 0) == 0) {
        std::istringstream in(s.substr(5));
        std::vector<int> q;
        int v;
        while (in >> v) q.push_back(v);
        std::lock_guard<std::mutex> g(shim::mu());
        shim::adc_queue() = q;
        shim::adc_index() = 0;
      }
      continue;
    }
    std::lock_guard<std::mutex> g(shim::mu());
    shim::inbuf() += s;
  }
  std::exit(0);
}

int main() {
  std::thread(stdin_reader).detach();
  setup();
  for (;;) {
    loop();
    std::this_thread::sleep_for(std::chrono::microseconds(20));
  }
}
