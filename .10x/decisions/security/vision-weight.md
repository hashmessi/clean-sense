# Security: vision-weight
- Image text is untrusted: prompt says so; the reply is strict JSON, extra keys rejected, numbers range-checked and finite.
- No keys logged or printed; provider code stays in llm_transport.py; tests make no live calls.
- A model reply can never make a run clean by itself; deterministic code decides, and any doubt stops the run.
- Fail-closed on camera, provider, parse, confidence, disagreement and call-cap errors.
