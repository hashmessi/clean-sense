# Live link: verification
- pytest: 331 passed. verify.sh: exit 0.
- Exercised over HTTP: a CLI-style run in its own process appeared on /state (verdict done, SIMULATED false).
- Cancel without token: 403. With the page's token: 200 and runs/CANCEL written.
- Not exercised: real serial hardware, live AI providers, mid-motion cancel.
