"""Canonical sport_key → sport_group mapping and market selection for the odds engine."""

_BASKETBALL: dict[str, str] = {
    "basketball_ncaab": "NCAAB",
    "basketball_nba": "NBA",
}

_OUTRIGHT_PREFIXES = ("golf_",)
_STANDARD_MARKETS = ["h2h", "spreads", "totals"]
_OUTRIGHT_MARKETS = ["outrights"]


def sport_group(sport_key: str) -> str:
    if sport_key.startswith("tennis_atp_"):
        return "ATP"
    if sport_key.startswith("tennis_wta_"):
        return "WTA"
    if sport_key.startswith("basketball_"):
        return _BASKETBALL.get(sport_key, sport_key.split("_")[1].upper())
    return sport_key.split("_")[0].title()


def markets_for_sport(sport_key: str) -> list[str]:
    """Return the appropriate market keys for a given sport_key."""
    if any(sport_key.startswith(p) for p in _OUTRIGHT_PREFIXES):
        return _OUTRIGHT_MARKETS
    return _STANDARD_MARKETS
