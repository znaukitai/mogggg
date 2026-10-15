import asyncio
import logging
import time
from dataclasses import dataclass

import config

log = logging.getLogger(__name__)


@dataclass
class Entry:
    tg_id: int
    chat_id: int
    name: str
    elo: int
    score: float
    joined: float  # time.monotonic()


class Matchmaker:
    """In-memory queue. Everything runs in one event loop, so no locks are needed."""

    def __init__(self) -> None:
        self.queue: dict[int, Entry] = {}

    def __contains__(self, tg_id: int) -> bool:
        return tg_id in self.queue

    def add(self, entry: Entry) -> None:
        self.queue[entry.tg_id] = entry

    def remove(self, tg_id: int) -> bool:
        return self.queue.pop(tg_id, None) is not None

    @staticmethod
    def search_range(entry: Entry, now: float) -> int:
        """+-200 at first, +100 every 30 seconds of waiting."""
        steps = int((now - entry.joined) // config.MATCH_RANGE_EVERY)
        return config.MATCH_RANGE + steps * config.MATCH_RANGE_STEP

    def pop_pairs(self, now: float) -> list[tuple[Entry, Entry]]:
        pairs = []
        free = sorted(self.queue.values(), key=lambda e: e.joined)  # longest waiting first
        used: set[int] = set()
        for a in free:
            if a.tg_id in used:
                continue
            best, best_diff = None, None
            for b in free:
                if b.tg_id == a.tg_id or b.tg_id in used:
                    continue
                diff = abs(a.elo - b.elo)
                # either player's range is enough, so a long waiter can meet a newcomer
                if diff <= max(self.search_range(a, now), self.search_range(b, now)):
                    if best_diff is None or diff < best_diff:
                        best, best_diff = b, diff
            if best:
                used.update((a.tg_id, best.tg_id))
                pairs.append((a, best))
        for a, b in pairs:
            self.queue.pop(a.tg_id, None)
            self.queue.pop(b.tg_id, None)
        return pairs

    def pop_expired(self, now: float) -> list[Entry]:
        gone = [e for e in self.queue.values() if now - e.joined > config.MATCH_TIMEOUT]
        for e in gone:
            self.queue.pop(e.tg_id, None)
        return gone


matchmaker = Matchmaker()


async def run_loop(on_match, on_timeout, interval: float = 2.0) -> None:
    """Background task: pairs players and reports timeouts via async callbacks."""
    while True:
        await asyncio.sleep(interval)
        try:
            now = time.monotonic()
            for a, b in matchmaker.pop_pairs(now):
                asyncio.create_task(_safe(on_match(a, b)))
            for e in matchmaker.pop_expired(now):
                asyncio.create_task(_safe(on_timeout(e)))
        except Exception:
            log.exception("matchmaking loop error")


async def _safe(coro) -> None:
    try:
        await coro
    except Exception:
        log.exception("matchmaking callback error")
