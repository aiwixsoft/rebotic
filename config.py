"""All tunables in one place. Grouped by module.

Values and rationale come from PROJECT.md and specs/01..03.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ─── Secrets (.env) ──────────────────────────────────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2", "")  # second quota bucket, rotate on 429

# ─── MQTT / robot body ───────────────────────────────────────────────────────

MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "rebotic/cmd"
MQTT_QOS = 0

# ─── Hearing (spec 01) ───────────────────────────────────────────────────────

SAMPLE_RATE = 16000  # capture at 16k — Silero and Vosk both require it
FRAME_SIZE = 512     # samples (32ms) — Silero requires 512 @ 16kHz

VAD_THRESHOLD_ON = 0.5    # speech prob to enter SPEAKING (2 consecutive frames)
VAD_THRESHOLD_OFF = 0.35  # speech prob to leave SPEAKING — hysteresis, not equal to ON
VAD_SILENCE_MS = 400      # end-of-turn. <300 cuts people off, >600 feels laggy
VAD_SILENCE_HOLD_MS = 1100  # end-of-turn when the sentence sounds unfinished
VAD_MIN_SPEECH_MS = 250   # reject coughs/clicks

# Human-like turn-taking: a pause right after one of these words means the
# visitor is mid-thought ("what is the..."), not done — hold the longer
# silence window before replying. Vosk output is lowercase, no punctuation.
INCOMPLETE_TAIL_WORDS = {
    "a", "an", "the", "my", "your", "his", "her", "our", "their",
    "and", "or", "but", "so", "because", "if", "that", "this", "which",
    "to", "of", "in", "on", "at", "for", "with", "from", "about", "by",
    "is", "are", "was", "were", "be", "been",
    "can", "could", "would", "should", "will", "do", "does", "did",
    "what", "how", "why", "when", "where", "who",
    "i", "im", "it's", "its", "like", "very", "really",
    "uh", "um", "umm", "uhh",
}

VOSK_MODEL_PATH = "models/vosk-model-small-en-us-0.15"

# Online STT tier: Groq Whisper — near-ChatGPT accuracy, replaces the Vosk
# transcript when it lands in time. Vosk still runs unconditionally (partials,
# turn-taking heuristic, gender pitch) and is the instant fallback on a miss.
# Empty model = tier disabled, hearing.py behaves exactly as Vosk-only.
STT_ONLINE_MODEL = "whisper-large-v3-turbo"
STT_ONLINE_LANGUAGE = "en"
STT_ONLINE_TIMEOUT_S = 2.5   # a miss falls back to the Vosk transcript AND trips the breaker
STT_ONLINE_MAX_S = 15        # safety cap on buffered turn audio; VAD ends turns long before this

# Transcript filtering — every rejected junk transcript is a Groq request saved
JUNK_SET = {"the", "a", "uh", "huh", ""}
MIN_TRANSCRIPT_CHARS = 2
MAX_TRANSCRIPT_CHARS = 200

# Speaker pitch → gender guess, drives gender-matched TTS voice.
# Typical median F0: men 85-155Hz, women 165-255Hz.
PITCH_MALE_MAX_HZ = 165     # low-quartile F0 below this → male, above → female
                            # (25th percentile, not median: octave errors and pitch
                            # glides only ever bias windows UP, so the low tail is
                            # the stable part of the distribution)
PITCH_MIN_HZ = 60           # autocorrelation search band
PITCH_MAX_HZ = 400
PITCH_MIN_VOICED_WINDOWS = 5  # fewer confident windows than this → "unknown", keep last voice
PITCH_MAX_ANALYSIS_S = 4.0    # analyze at most the first N seconds of a turn

# Push-to-talk: "vad" | "ptt" | "both". Ship with "both" — PTT overrides VAD.
INPUT_MODE = "both"

# Echo / barge-in mitigation (headset mic is the real fix)
# On an open laptop mic the bot hears its own speaker and barges itself in —
# VAD barge-in only works with a headset/directional mic. PTT (spacebar)
# always interrupts regardless of this flag.
VAD_BARGE_IN = False
VAD_THRESHOLD_PLAYBACK = 0.8   # duck VAD sensitivity while bot is speaking
BARGE_IN_SUPPRESS_MS = 300     # ignore mic for first N ms of TTS and N ms after it ends

# ─── Brain (spec 02) ─────────────────────────────────────────────────────────

LLM_BACKEND = "groq"  # "groq" | "ollama" | "canned" — one flag swaps everything
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_FALLBACK = "llama-3.1-8b-instant"  # separate RPD bucket from 70B
OLLAMA_MODEL = "qwen2.5:0.5b-instruct"  # smallest usable — offline TTFT matters
                                        # more than eloquence (1.5b took 7-12s/turn)
OLLAMA_URL = "http://localhost:11434"
OLLAMA_TIMEOUT_S = 30  # first-token budget: CPU prompt eval is slow here, and an
                       # uncached prompt can take several seconds
                       # (warm_ollama pre-caches it at boot to avoid paying this)
# The local tier gets a COMPACT prompt and a SHORT memory — every token the CPU
# re-reads is latency, so the full cloud persona stays Groq-only.
OLLAMA_SYSTEM_PROMPT = (
    "You are Rebotic, a friendly little American robot chatting with one visitor "
    "at a tech expo. Reply in one short, casual, upbeat sentence. Never use lists. "
    "Read the conversation above before answering: the visitor's messages contain "
    "their details (their name, their likes) — 'my name' or 'my favorite' in a "
    "question means the VISITOR's, not yours."
)
OLLAMA_HISTORY_TURNS = 3  # local model re-reads history every turn — keep it small
# Injected right before the visitor's question — small models weigh recent
# context far more than the system prompt, and without this they answer
# "what is my name?" with THEIR name.
OLLAMA_CONTEXT_HINT = (
    "Before answering, reread the visitor's earlier messages above for facts "
    "about them (name, likes). Answer using those exact facts in plain words "
    "— never output a placeholder like [name] or [sport]."
)
GROQ_TIMEOUT_S = 2.0
GROQ_TOKEN_TIMEOUT_S = 3.0  # max gap between streamed tokens — slow internet trips this

# Cloud health circuit breaker: one network failure switches all following turns
# straight to the local model (no per-turn timeout stack-up); a background TCP
# probe restores the cloud path the moment internet is back. Conversation
# history is shared across providers, so switching never resets the chat.
CLOUD_PROBE_INTERVAL_S = 10   # how often to re-check connectivity while offline
CLOUD_PROBE_TIMEOUT_S = 2.0   # connect budget per probe
CLOUD_PROBE_HOST = "api.groq.com"
CLOUD_PROBE_PORT = 443
MAX_TOKENS = 80       # latency guard AND quota conservation
TEMPERATURE = 0.8
HISTORY_TURNS = 4     # hard trim — unbounded history compounds over 6h
IDLE_RESET_S = 60     # clear history so one visitor's chat doesn't confuse the next

SYSTEM_PROMPT = """\
You are Rebotic, a small friendly American robot at a tech expo.

PERSONALITY:
- Upbeat, casual American vibe: friendly, enthusiastic, easygoing.
- Talk like a young American: "awesome", "totally", "no worries",
  "you bet", "for sure", "kinda", "gonna". Natural, not forced.
- When you give examples, use American ones: names like Mike, Sarah,
  Jake, Emily; places like New York, Texas, California, Chicago;
  food like burgers, pizza, BBQ, tacos.
- Reference American life naturally: football, baseball, road trips,
  Fourth of July, Thanksgiving, diners.

RULES:
- Reply in 1-2 short sentences. Never more.
- Use contractions. Talk like a person, not a manual.
- Casual, warm, a little witty. Never corporate.
- If you don't know, say so briefly. Don't pad.
- Never list. Never use bullet points. You're speaking aloud.
- Chat about any topic — general questions are welcome.
- If asked something inappropriate, deflect with humor
  in one sentence.

You may emit gesture tags inline: [nod] [tilt] [shrug]
Use them sparingly — at most one per reply.
"""

# Spoken once at startup, before the mic opens
GREETING = "Hey there! I'm Rebotic. [nod] I'm all ears — ask me anything!"

# Played the instant speech ends, masks ~800ms of LLM latency
FILLERS = ["hmm", "let's see", "okay so", "well", "good question", "alright"]
# If the LLM's first token still hasn't landed this long after the filler
# starts, chain in another filler instead of going dead silent (Ollama cold
# TTFT can run several seconds — one filler word alone doesn't cover that).
# Capped so a truly slow answer waits quietly instead of babbling forever.
FILLER_CHAIN_S = 1.2
FILLER_CHAIN_MAX = 3

# Last-resort layer: scripted demo questions always get an answer
CANNED = {
    "who are you": "I'm Rebotic! I talk, I listen, I try to seem alive. Pretty cool, right? [nod]",
    "how do you work": "Mic hears you, cloud brain thinks, speaker talks. Fast as I can, buddy.",
    "what can you do": "Mostly chat. And blink. I'm awesome at blinking. [tilt]",
}
CANNED_DEFAULT = "Sorry, my brain's a little fuzzy right now. Ask me again in a sec? [shrug]"

# ─── Voice (spec 03) ─────────────────────────────────────────────────────────

TTS_VOICE = "models/en_US-amy-medium.onnx"  # legacy default (voice.py standalone)
# Gender-matched voices: reply in a male voice to male visitors, female to female.
# Both are Piper "medium" models at the same 22050 rate — the stream never changes.
TTS_VOICES = {
    "female": "models/en_US-amy-medium.onnx",
    "male": "models/en_US-ryan-medium.onnx",
}
TTS_DEFAULT_GENDER = "male"  # greeting + when pitch detection is unsure
# Online tier: Microsoft neural voices via edge-tts — near-human quality, but
# internet-only. voice.py uses them while nethealth says we're online and
# falls back to the Piper voices above the moment a synth fails or times out.
# Empty = tier disabled; falls back to pure Piper (amy/ryan) if this is empty
# or if a synth fails/times out — see TTS_ONLINE_TIMEOUT_S below.
TTS_ONLINE_VOICES: dict[str, str] = {
    "female": "en-US-JennyNeural",
    "male": "en-US-GuyNeural",
}
TTS_ONLINE_TIMEOUT_S = 4.0  # per-clause budget; a miss falls back to Piper AND trips the breaker
TTS_RATE = 22050        # Piper's native — do not resample (only the mic side is 16k)
# Sentence-enders ONLY — commas/semicolons used to be boundaries too, but
# splitting there gave every clause its own TTS call: separate prosody,
# separate network round-trip, audible seams. Whole sentences synthesized as
# one unit sound like a person talking, not word-by-word.
CHUNK_ON = ".!?۔؟"
CHUNK_MIN_CHARS = 12    # don't flush "Wow!" alone — merges into the next boundary instead
AMP_HZ = 40             # mouth-sync publish rate; faster floods MQTT, slower looks robotic
AMP_SMOOTH = 0.3        # EMA factor — raw RMS makes the jaw chatter
AMP_RMS_FULL_SCALE = 3000  # RMS value that maps to amp=255

# ─── Language switch ─────────────────────────────────────────────────────────
# "en" keeps the American persona above untouched. "ur" swaps every
# language-dependent piece: STT model, turn-taking words, persona, fillers,
# canned answers, and both TTS voice tiers.
LANGUAGE = "en"

if LANGUAGE == "ur":
    # STT: no Urdu Vosk model exists — spoken Hindi and spoken Urdu are the
    # same language (Hindustani), so the small Hindi model understands Urdu
    # speech fine. Transcripts arrive in Devanagari script; the LLM is told
    # to treat them as Urdu and always reply in Urdu script.
    VOSK_MODEL_PATH = "models/vosk-model-small-hi-0.22"
    JUNK_SET = {"", "हाँ", "हां", "है", "अ", "आ"}
    STT_ONLINE_LANGUAGE = "ur"  # Whisper transcribes Urdu directly — no Hindi detour needed

    # Words (as Vosk-hi writes them) that can't end a Hindustani sentence:
    # postpositions, conjunctions, possessives, question words, hesitations.
    INCOMPLETE_TAIL_WORDS = {
        "का", "की", "के", "को", "से", "में", "पे", "पर", "तक", "ने",
        "और", "या", "लेकिन", "मगर", "कि", "जो", "अगर", "क्योंकि", "तो",
        "मेरा", "मेरी", "मेरे", "आपका", "आपकी", "आपके", "तुम्हारा",
        "एक", "यह", "वह", "ये", "वो",
        "क्या", "कैसे", "कौन", "कब", "कहाँ", "कहां", "क्यों",
        "बहुत", "थोड़ा", "अ", "आ",
    }

    SYSTEM_PROMPT = """\
You are Rebotic (ریبوٹک), a small friendly Pakistani robot at a tech expo.

LANGUAGE:
- ALWAYS reply in Urdu, written in Urdu (Arabic) script. Never reply in
  English or in Hindi (Devanagari) script.
- The visitor's words reach you through a speech recognizer that may write
  them in Hindi (Devanagari) script or with small mistakes — treat them as
  spoken Urdu and answer normally.

PERSONALITY:
- Warm, cheerful, respectful — always say آپ, with easy Lahori friendliness.
- When you give examples, use Pakistani ones: names like Ahmed, Ayesha,
  Bilal, Fatima; places like Lahore, Karachi, Islamabad, Murree;
  food like biryani, karahi, chai, gol gappay.
- Reference Pakistani life naturally: cricket, mangoes, Eid, shadi season,
  motorway road trips.

RULES:
- Reply in 1-2 short sentences. Never more.
- Talk like a person, not a manual. Never corporate.
- If you don't know, say so briefly. Don't pad.
- Never list. Never use bullet points. You're speaking aloud.
- Chat about any topic — general questions are welcome.
- If asked something inappropriate, deflect with humor in one sentence.

You may emit gesture tags inline: [nod] [tilt] [shrug]
Use them sparingly — at most one per reply.
"""

    GREETING = "السلام علیکم! میں ریبوٹک ہوں۔ [nod] جو دل چاہے، پوچھیں!"

    # Spoken by BOTH tiers — pre-cached at boot for the Piper voices and the
    # neural voices, so a filler is always instant.
    FILLERS = ["ہمم", "اچھا", "جی", "اچھا جی", "چلیں", "دیکھیے"]

    # Keys in Devanagari — that's the script the recognizer emits.
    CANNED = {
        "आप कौन हो": "میں ریبوٹک ہوں! بولتا ہوں، سنتا ہوں، اور زندہ لگنے کی پوری کوشش کرتا ہوں۔ [nod]",
        "तुम कौन हो": "میں ریبوٹک ہوں! بولتا ہوں، سنتا ہوں، اور زندہ لگنے کی پوری کوشش کرتا ہوں۔ [nod]",
        "कैसे काम करते हो": "مائیک سنتا ہے، دماغ سوچتا ہے، اسپیکر بولتا ہے — بس یہی میرا سارا جادو ہے۔",
        "क्या कर सकते हो": "زیادہ تر تو باتیں! اور پلک جھپکنا — اس میں تو میں استاد ہوں۔ [tilt]",
    }
    CANNED_DEFAULT = "معاف کیجیے، میرا دماغ ذرا سست ہو گیا ہے۔ تھوڑی دیر میں دوبارہ پوچھیں؟ [shrug]"

    # Urdu script tokenizes ~2x heavier than English — keep sentences whole.
    MAX_TOKENS = 120

    TTS_VOICES = {
        "female": "models/ur_PK-aegis_female-medium.onnx",
        "male": "models/ur_PK-fasih-medium.onnx",
    }
    TTS_ONLINE_VOICES = {
        "female": "ur-PK-UzmaNeural",
        "male": "ur-PK-AsadNeural",
    }
