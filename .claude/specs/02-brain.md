# Spec 02 — BRAIN

**Module:** `brain.py`
**Job:** Transcript + history → streamed response tokens.
**Owns:** Conversation state only. Nothing else.

---

## Pipeline

```
final transcript
  → append to history (trimmed)
  → [system prompt + persona + last 4 turns]
  → LLM provider (cascade)
  → token stream out
```

---

## Provider Cascade

```
1. Groq  llama-3.3-70b-versatile   2s timeout  ─429/fail→
2. Groq  llama-3.1-8b-instant      separate quota bucket  ─fail→
3. Ollama qwen2.5:1.5b-instruct    offline, ~600ms  ─fail→
4. Canned response                 never silent
```

Three lives before the fallback. Layer 2 matters because **each Groq model has its own RPD bucket** — exhausting 70B does not exhaust 8B.

### Key rotation
Two Groq keys (two Google accounts = two quotas). On 429, rotate key *first*, then downgrade model:

```
key1+70B → key2+70B → key1+8B → key2+8B → ollama → canned
```

Six lives. Costs ~15 lines.

---

## Config

```python
LLM_BACKEND    = "groq"   # "groq" | "ollama" | "canned"  — one flag swaps everything
GROQ_MODEL     = "llama-3.3-70b-versatile"
GROQ_FALLBACK  = "llama-3.1-8b-instant"
OLLAMA_MODEL   = "qwen2.5:1.5b-instruct"
GROQ_TIMEOUT_S = 2.0
MAX_TOKENS     = 80
TEMPERATURE    = 0.8
HISTORY_TURNS  = 4
IDLE_RESET_S   = 60
```

`LLM_BACKEND = "ollama"` + `qwen-fast` → pipeline wiring tests. Instant, quality irrelevant when you're only checking tokens flow. Flip to `"groq"` for real tests.

---

## The Model Problem

**Do not build on `qwen2:0.5b` or `tinyllama`.** They cannot hold a persona or follow "be brief." You will debug a pipeline that is working fine.

On CPU-only i5 8th gen:

| Model | TTFT | Verdict |
|---|---|---|
| qwen2:0.5b | ~150ms | Incoherent |
| qwen2.5:1.5b | ~600ms | Floor for coherence. Offline fallback only. |
| qwen2.5:3b | ~1-2s | Too slow |
| Groq 70B | ~100ms | **140× bigger AND faster than local** |

There is no speed/quality tradeoff with Groq. It wins both. Local only wins offline.

---

## System Prompt

The persona **is** the human-feel. Constrain hard.

```
You are Rebotic, a small friendly robot at a tech expo.

RULES:
- Reply in 1-2 short sentences. Never more.
- Use contractions. Talk like a person, not a manual.
- Casual, warm, a little witty. Never corporate.
- If you don't know, say so briefly. Don't pad.
- Never list. Never use bullet points. You're speaking aloud.
- Stay on: yourself, this expo, robots, tech.
- If asked something off-topic or inappropriate, deflect
  with humor in one sentence and redirect.

You may emit gesture tags inline: [nod] [tilt] [shrug]
Use them sparingly — at most one per reply.
```

**Why so tight:** open-domain = it says something dumb in front of judges. A bot that says "ask me about my mission" outperforms one that answers anything, because you control the question space.

---

## Streaming (mandatory)

```python
stream=True
```

Yield tokens as they arrive. Never wait for completion. The voice module chunks on clause boundaries and starts speaking on the **first** one — that's where the sub-1s number comes from.

`max_tokens: 80` is both a latency guard and quota conservation. Long replies = dead air = broken illusion.

---

## Abortable

Barge-in must kill generation mid-stream.

```python
self._abort = asyncio.Event()

async for chunk in stream:
    if self._abort.is_set():
        break            # close the stream, discard
    yield token
```

Non-negotiable. A brain you can't interrupt makes barge-in impossible upstream.

---

## History Management

```python
history = history[-(HISTORY_TURNS * 2):]   # 4 turns = 8 messages
```

Hard trim, every turn. Unbounded history at a 6-hour expo = RAM climb + rising latency + rising token spend. All three compound.

**Reset on idle:**
```python
if now - last_interaction > IDLE_RESET_S:
    history.clear()
```
Also stops one visitor's weird conversation from confusing the next.

---

## Filler (the latency cheat)

On `speech_end`, emit **immediately** — before calling the LLM:

```python
FILLERS = ["hmm", "let's see", "okay so", "right", "good question"]
yield random.choice(FILLERS)
```

Plays while the LLM thinks. Masks ~800ms. Costs nothing, buys everything. This is the highest-value 5 lines in the project.

---

## Canned Layer (expo insurance)

For your 5-6 scripted demo questions, hardcode good answers. Fuzzy-match the transcript:

```python
CANNED = {
    "who are you":    "I'm Rebotic! I talk, I listen, I try to seem alive. [nod]",
    "how do you work": "Mic hears you, cloud brain thinks, speaker talks. Fast as I can.",
    "what can you do": "Mostly chat. And blink. I'm very good at blinking. [tilt]",
    ...
}
```

If Groq is down, Ollama is down, and someone asks a scripted question — **the bot still talks.** Judges never see a dead robot.

---

## Rate Limits

Groq free tier enforces **RPM / RPD / TPM / TPD**, per model, whichever hits first.

- **RPM is irrelevant** — one robot, one conversation, ~6-10 req/min peak.
- **RPD is the wall.** Rough math: 6h × conversation every 2min × 4 turns ≈ **700 requests**.

### Actions
- Sign up → Settings → Limits → **screenshot your real RPD.** That's your budget, not a guess.
- Log rate-limit headers on every response:
  ```
  x-ratelimit-remaining-requests
  x-ratelimit-remaining-tokens
  x-ratelimit-reset-requests
  ```
  You'll see the wall coming instead of hitting it.
- On 429: read `retry-after`, rotate key, don't retry blindly.
- Filter junk transcripts upstream (spec 01) — every one saved is quota banked.

---

## Interface

```python
class Brain:
    def __init__(self, bus: asyncio.Queue, config): ...
    async def respond(self, text: str) -> AsyncIterator[str]: ...
    def abort(self): ...
    def reset(self): ...
```

---

## Standalone Test

```bash
python brain.py          # REPL: type text, watch tokens stream
```

**Passes when:**
- First token < 300ms (Groq)
- Replies are 1-2 sentences, never a list
- Persona holds over 10 turns
- Ctrl-C mid-stream aborts cleanly, no hang
- Kill your WiFi → falls through to Ollama within 2s, still replies
- Kill Ollama too → canned response fires, still replies
- History stays at 4 turns after 20 exchanges

**Test the fallback path for real.** Do not discover it's broken at hour 5 of the expo.

---

## Failure Modes

| Symptom | Cause |
|---|---|
| Long rambling replies | `max_tokens` unset, or persona too loose |
| Bot lists things | Prompt not forbidding lists — it's *speaking*, not writing |
| Slow every hour | History not trimmed |
| Hangs on WiFi drop | No timeout on Groq call |
| 429 at hour 4 | Junk transcripts burned quota; no key rotation |
| Fallback broken on stage | Never tested offline path |
