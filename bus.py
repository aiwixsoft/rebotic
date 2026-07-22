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


def final(text: str, gender: str = "unknown") -> Event:
    """Finalized user turn — triggers the brain. gender is a pitch-based
    guess ("male"/"female"/"unknown") used to match the TTS voice."""
    return Event("final", {"text": text, "gender": gender})


def barge_in() -> Event:
    """User spoke while bot was talking — abort brain + voice."""
    return Event("barge_in")


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
