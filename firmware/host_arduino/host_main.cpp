// Host build of cleansense_a.ino: the real sketch, with the Arduino shim underneath.
#include <Arduino.h>
#include "cleansense_a.ino"

#include <thread>

static void stdin_reader() {
  char line[512];
  while (std::fgets(line, sizeof line, stdin)) {
    std::string s(line);
    if (!s.empty() && s[0] == '#') {  // harness control, never shown to the sketch
      int a = 0, b = 0;
      if (std::sscanf(s.c_str(), "#pin %d %d", &a, &b) == 2) {
        std::lock_guard<std::mutex> g(shim::mu());
        shim::pins()[a] = b;
      } else if (std::sscanf(s.c_str(), "#hx %d", &a) == 1) {
        std::lock_guard<std::mutex> g(shim::mu());
        shim::hx_value() = a;
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
    std::this_thread::sleep_for(std::chrono::microseconds(500));
  }
}
