from enum import StrEnum


class EventStatus(StrEnum):
    upcoming = "upcoming"
    live = "live"
    completed = "completed"


class MarketKey(StrEnum):
    h2h = "h2h"
    spreads = "spreads"
    totals = "totals"


class BookmakerKey(StrEnum):
    draftkings = "draftkings"
    fanduel = "fanduel"
    betmgm = "betmgm"
    caesars = "caesars"
    bovada = "bovada"
    betonlineag = "betonlineag"
