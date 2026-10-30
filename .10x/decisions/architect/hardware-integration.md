# Architect: hardware-integration
See ADR-004. The handoff folder is a bench console only. Run firmware stays `cleansense_a` (pin 18, JSON, heartbeat, E-stop, limits.h). Servo wire moves D13 to 18. Home 90 degrees; bench the arm at 45 and 135 before any run. Wrong-sketch failure is fail-closed (handshake times out).
