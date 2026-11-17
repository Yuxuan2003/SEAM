"""Browser-tab descriptors shared by the domain scaffolds."""
from __future__ import annotations


def _tab(title, letter, color, on=False):
    return {"title": title, "letter": letter, "color": color, "on": on}


MAIL = _tab("Inbox (3) — Mail", "M", "#1a73e8")
CAL = _tab("Calendar — Week 43", "C", "#188038")
