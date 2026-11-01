"""Async event bus: one asyncio.Queue, typed helpers for every event on it.

Modules never call each other — they put events here and main.py routes them.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


# ─── Typed event constructors ────────────────────────────────────────────────

def speech_start() -> Event:
    """VAD detected speech — robot must attend immediately."""
    return Event("speech_start")


def partial(text: str) -> Event:
    """Live in-progress transcript (debug/UI only)."""
    return Event("partial", {"text": text})


def final(text: str, gender: str = "unknown", turn_id: int = 0) -> Event:
    """Finalized user turn — triggers the brain. gender is a pitch-based
    guess ("male"/"female"/"unknown") used to match the TTS voice. turn_id
    is a monotonically increasing per-utterance counter (set by hearing.py)
    so main.py can discard a late-arriving final from an older utterance's
    backgrounded online-STT lookup instead of letting it interrupt a newer,
    already-in-progress reply."""
    return Event("final", {"text": text, "gender": gender, "turn_id": turn_id})


def barge_in() -> Event:
    """User spoke while bot was talking — abort brain + voice."""
    return Event("barge_in")


def new_session() -> Event:
    """PTT tap (short press) — visitor signals a fresh conversation.
    Ends whatever session was active and starts a new one."""
    return Event("new_session")


# ─── Bus ─────────────────────────────────────────────────────────────────────

class Bus:
    """Thin wrapper over asyncio.Queue so producers/consumers share one API."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue()

    def publish(self, event: Event) -> None:
        self._queue.put_nowait(event)

    def publish_threadsafe(self, loop: asyncio.AbstractEventLoop, event: Event) -> None:
        """For audio-callback threads (sounddevice) that can't touch the loop."""
        loop.call_soon_threadsafe(self._queue.put_nowait, event)

    async def get(self) -> Event:
        return await self._queue.get()
