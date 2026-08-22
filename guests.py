"""Special-guest roster — loads special-gest.md and matches spoken names
against it so main.py can trigger a personalized welcome (see brain.py's
welcome_special_guest) instead of the generic visitor-session ack.

File format (special-gest.md), one block per guest, blank-line separated:
    ## Full Name
    Title: their title
    Background: their background, one paragraph
    Contributions: their contribution, one paragraph
"""

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("guests")

_ENTRY_RE = re.compile(
    r"^##\s*(?P<name>.+?)\s*\n"
    r"Title:\s*(?P<title>.+?)\s*\n"
    r"Background:\s*(?P<background>.+?)\s*\n"
    r"Contributions:\s*(?P<contributions>.+)$",
    re.DOTALL,
)

_WORD_RE = re.compile(r"[^\w\s]")


@dataclass
class Guest:
    name: str
    title: str
    background: str
    contributions: str


class Roster:
    def __init__(self, guests: list[Guest]) -> None:
        self.guests = guests
        # Precomputed lowercase name-token sets, shared by match() and match_in_text()
        self._tokens = [set(g.name.lower().split()) for g in guests]

    def match(self, spoken_name: str) -> Guest | None:
        """Best-effort match of an already-extracted (<=2 word) spoken name
        against the roster. Ambiguous or weak matches return None — a wrong
        VIP greeting is worse than falling back to the generic ack."""
        words = [w.lower() for w in spoken_name.split()]
        if not words:
            return None

        if len(words) >= 2:
            wanted = set(words)
            hits = [g for g, toks in zip(self.guests, self._tokens) if wanted <= toks]
            return hits[0] if len(hits) == 1 else None

        word = words[0]
        hits = [g for g, toks in zip(self.guests, self._tokens) if word in toks]
        return hits[0] if len(hits) == 1 else None

    def match_in_text(self, text: str) -> Guest | None:
        """Scan a full raw transcript for a roster name, rather than relying
        on _extract_name's generic <=2-word cap — VIPs are often introduced
        with a title attached ("I'm the Chief Minister, Sohail Afridi"),
        which the generic extractor mangles into just "Chief Minister".
        Requires >=2 overlapping name tokens (or a full single-token name)
        so a shared surname alone ("Khan" — 3 of 5 guests here) never
        guesses wrong; a wrong VIP greeting is worse than falling back to
        the generic ack."""
        words = set(_WORD_RE.sub("", text.lower()).split())
        hits = []
        for g, toks in zip(self.guests, self._tokens):
            overlap = toks & words
            if len(overlap) >= 2 or (len(toks) == 1 and toks <= words):
                hits.append(g)
        return hits[0] if len(hits) == 1 else None


def load(path: str) -> Roster:
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        log.warning("special-guest roster %s not readable: %r — no VIP welcomes this run", path, e)
        return Roster([])

    guests = []
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        m = _ENTRY_RE.match(block)
        if not m:
            log.warning("skipping unparsable roster entry: %r", block[:60])
            continue
        guests.append(Guest(
            name=m.group("name").strip(),
            title=m.group("title").strip(),
            background=m.group("background").strip(),
            contributions=m.group("contributions").strip(),
        ))
    log.info("loaded %d special guest(s) from %s", len(guests), path)
    return Roster(guests)
