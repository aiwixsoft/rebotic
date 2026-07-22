"""Shared internet-health state — one circuit breaker for every cloud tier.

brain (Groq) and voice (edge-tts) both depend on the internet; when either
one sees a network failure it trips this shared breaker and BOTH fall back
to their local tier on the same turn. A single background TCP probe flips
the flag back when the internet returns, so recovery never costs a
conversation turn. Modules still never call each other — they share this
passive state object.
"""

import asyncio
import logging

log = logging.getLogger("nethealth")


class NetHealth:
    def __init__(self, config) -> None:
        self.cfg = config
        self.ok = True
        self.simulate_offline = False  # test hook: REPL /offline holds the breaker open
        self._probe_task: asyncio.Task | None = None

    def mark_down(self) -> None:
        """Trip the breaker: every cloud tier switches to local until the
        probe sees the internet again."""
        if not self.ok:
            return
        self.ok = False
        log.info("internet marked down — all cloud tiers switch to local")
        if self._probe_task is None or self._probe_task.done():
            self._probe_task = asyncio.create_task(self._probe_loop())

    async def _probe_loop(self) -> None:
        """Cheap TCP reachability check every CLOUD_PROBE_INTERVAL_S. Runs
        only while the breaker is tripped."""
        while not self.ok:
            await asyncio.sleep(self.cfg.CLOUD_PROBE_INTERVAL_S)
            if self.simulate_offline:
                continue
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        self.cfg.CLOUD_PROBE_HOST, self.cfg.CLOUD_PROBE_PORT),
                    timeout=self.cfg.CLOUD_PROBE_TIMEOUT_S,
                )
                writer.close()
                self.ok = True
                log.info("internet is back — cloud tiers restored")
            except (OSError, TimeoutError):
                pass  # still offline, keep probing


def _default() -> NetHealth:
    import config
    return NetHealth(config)


health = _default()
