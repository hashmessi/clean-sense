# Live link: security review
- Cancel is token-protected and not readable cross-origin. Token is random per server start.
- Reads (/state, /runs, /results) are unauthenticated and localhost-only. Accepted for a bench tool.
- No secrets in /state, /runs or the page. Provider names only; keys never leave .env.
- Open: a dashboard bound to 0.0.0.0 would expose reads. Keep 127.0.0.1.
