"""Unit tests for src/odds_engine/services/enrichment.py.

Pure logic tests — no I/O, no DB, no Redis. All assertions use real fixture
data from tests/fixtures/odds_api/.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from odds_engine.schemas.odds_api import OddsAPIEvent
from odds_engine.services.enrichment import (
    build_enriched_event,
    compute_best_line,
    compute_consensus,
    compute_movement,
    compute_vig_free,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "odds_api"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_events(filename: str) -> list[OddsAPIEvent]:
    raw = json.loads((FIXTURES / filename).read_text())
    return [OddsAPIEvent.model_validate(e) for e in raw]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ncaab_events() -> list[OddsAPIEvent]:
    return load_events("odds_basketball_ncaab.json")


@pytest.fixture(scope="module")
def tennis_events() -> list[OddsAPIEvent]:
    return load_events("odds_tennis_atp_indian_wells.json")


@pytest.fixture(scope="module")
def ncaab_event(ncaab_events: list[OddsAPIEvent]) -> OddsAPIEvent:
    """First NCAAB event (Louisiana vs Georgia St — 4 bookmakers)."""
    return ncaab_events[0]


@pytest.fixture(scope="module")
def tennis_event(tennis_events: list[OddsAPIEvent]) -> OddsAPIEvent:
    """First tennis event — Galarneau vs Barrios Vera (1 bookmaker: bovada)."""
    return tennis_events[0]


# ---------------------------------------------------------------------------
# 1. test_compute_best_line_finds_highest_price
# ---------------------------------------------------------------------------


def test_compute_best_line_finds_highest_price(ncaab_event: OddsAPIEvent) -> None:
    best = compute_best_line(ncaab_event)

    assert "h2h" in best

    # Gather all individual book prices for each team in h2h.
    for team in [ncaab_event.home_team, ncaab_event.away_team]:
        individual_prices: list[float] = []
        for bk in ncaab_event.bookmakers:
            for mkt in bk.markets:
                if mkt.key != "h2h":
                    continue
                for oc in mkt.outcomes:
                    if oc.name == team:
                        individual_prices.append(oc.price)

        if individual_prices:
            best_price = best["h2h"][team]["price"]
            assert best_price == max(individual_prices), (
                f"Expected best h2h price for {team} to be {max(individual_prices)}, "
                f"got {best_price}"
            )


# ---------------------------------------------------------------------------
# 2. test_compute_best_line_identifies_correct_bookmaker
# ---------------------------------------------------------------------------


def test_compute_best_line_identifies_correct_bookmaker(ncaab_event: OddsAPIEvent) -> None:
    best = compute_best_line(ncaab_event)

    for team in [ncaab_event.home_team, ncaab_event.away_team]:
        entry = best["h2h"][team]
        best_price = entry["price"]
        best_bk = entry["bookmaker"]

        # Verify the claimed bookmaker actually has that exact price.
        found = False
        for bk in ncaab_event.bookmakers:
            if bk.key != best_bk:
                continue
            for mkt in bk.markets:
                if mkt.key != "h2h":
                    continue
                for oc in mkt.outcomes:
                    if oc.name == team and oc.price == best_price:
                        found = True
        assert found, (
            f"Bookmaker '{best_bk}' claimed to have price {best_price} for {team} "
            "in h2h, but it does not."
        )


# ---------------------------------------------------------------------------
# 3. test_compute_best_line_includes_point_for_spreads
# ---------------------------------------------------------------------------


def test_compute_best_line_includes_point_for_spreads(ncaab_event: OddsAPIEvent) -> None:
    best = compute_best_line(ncaab_event)

    assert "spreads" in best
    for outcome_name, entry in best["spreads"].items():
        assert "point" in entry, (
            f"Expected 'point' key in spreads best_line for outcome '{outcome_name}'"
        )
        assert isinstance(entry["point"], float | int)


# ---------------------------------------------------------------------------
# 4. test_compute_consensus_is_mean_of_prices
# ---------------------------------------------------------------------------


def test_compute_consensus_is_mean_of_prices(ncaab_event: OddsAPIEvent) -> None:
    consensus = compute_consensus(ncaab_event)

    # Manually compute expected consensus for Georgia St Panthers in h2h.
    team = "Georgia St Panthers"
    prices: list[float] = []
    for bk in ncaab_event.bookmakers:
        for mkt in bk.markets:
            if mkt.key != "h2h":
                continue
            for oc in mkt.outcomes:
                if oc.name == team:
                    prices.append(oc.price)

    assert prices, "No h2h prices found for Georgia St Panthers in fixture"
    expected_mean = sum(prices) / len(prices)
    assert consensus["h2h"][team]["price"] == pytest.approx(expected_mean, rel=1e-6)


# ---------------------------------------------------------------------------
# 5. test_compute_vig_free_sums_to_one
# ---------------------------------------------------------------------------


def test_compute_vig_free_sums_to_one(ncaab_event: OddsAPIEvent) -> None:
    vig_free = compute_vig_free(ncaab_event)

    for market_key, outcomes in vig_free.items():
        total_prob = sum(entry["implied_prob"] for entry in outcomes.values())
        assert total_prob == pytest.approx(1.0, abs=0.001), (
            f"Market '{market_key}': implied probs sum to {total_prob}, expected ~1.0"
        )


# ---------------------------------------------------------------------------
# 6. test_compute_vig_free_positive_odds_conversion
# ---------------------------------------------------------------------------


def test_compute_vig_free_positive_odds_conversion() -> None:
    """For price = +200, raw_prob = 100 / (200 + 100) = 0.3333..."""
    # Build a minimal synthetic event with a single bookmaker offering +200 / -200.
    raw = [
        {
            "id": "synthetic_positive",
            "sport_key": "test",
            "sport_title": "Test",
            "home_team": "TeamA",
            "away_team": "TeamB",
            "commence_time": "2026-06-01T12:00:00Z",
            "bookmakers": [
                {
                    "key": "testbook",
                    "title": "TestBook",
                    "last_update": "2026-06-01T11:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "last_update": "2026-06-01T11:00:00Z",
                            "outcomes": [
                                {"name": "TeamA", "price": 200},
                                {"name": "TeamB", "price": -200},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    event = OddsAPIEvent.model_validate(raw[0])
    vig_free = compute_vig_free(event)

    # raw_prob(+200) = 100 / (200 + 100) = 0.3333...
    # raw_prob(-200) = 200 / (200 + 100) = 0.6666...
    # total = 1.0 exactly (no vig in this synthetic case)
    # vig-free: TeamA = 0.3333 / 1.0 = 0.3333
    expected_team_a = round(100 / (200 + 100), 4)
    assert vig_free["h2h"]["TeamA"]["implied_prob"] == pytest.approx(
        expected_team_a, abs=0.001
    )


# ---------------------------------------------------------------------------
# 7. test_compute_vig_free_negative_odds_conversion
# ---------------------------------------------------------------------------


def test_compute_vig_free_negative_odds_conversion() -> None:
    """For price = -150, raw_prob = 150 / (150 + 100) = 0.6."""
    raw = [
        {
            "id": "synthetic_negative",
            "sport_key": "test",
            "sport_title": "Test",
            "home_team": "FavTeam",
            "away_team": "DogTeam",
            "commence_time": "2026-06-01T12:00:00Z",
            "bookmakers": [
                {
                    "key": "testbook",
                    "title": "TestBook",
                    "last_update": "2026-06-01T11:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "last_update": "2026-06-01T11:00:00Z",
                            "outcomes": [
                                {"name": "FavTeam", "price": -150},
                                {"name": "DogTeam", "price": 130},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    event = OddsAPIEvent.model_validate(raw[0])
    vig_free = compute_vig_free(event)

    # raw_prob(-150) = 150 / (150 + 100) = 0.6
    raw_fav = 150.0 / (150 + 100)
    raw_dog = 100.0 / (130 + 100)
    total = raw_fav + raw_dog
    expected_fav_vf = round(raw_fav / total, 4)
    assert vig_free["h2h"]["FavTeam"]["implied_prob"] == pytest.approx(
        expected_fav_vf, abs=0.001
    )


# ---------------------------------------------------------------------------
# 8. test_compute_movement_returns_empty_when_no_previous
# ---------------------------------------------------------------------------


def test_compute_movement_returns_empty_when_no_previous(
    ncaab_event: OddsAPIEvent,
) -> None:
    movement = compute_movement(ncaab_event, previous_bookmaker_odds=None)
    assert movement == {}

    movement_empty = compute_movement(ncaab_event, previous_bookmaker_odds=[])
    assert movement_empty == {}


# ---------------------------------------------------------------------------
# 9. test_compute_movement_detects_price_change
# ---------------------------------------------------------------------------


def test_compute_movement_detects_price_change(ncaab_event: OddsAPIEvent) -> None:
    """Construct previous snapshot with lower prices; expect positive price_delta."""
    # Use current odds minus 10 for all outcomes as the "previous" snapshot.
    previous: list[dict] = []
    for bk in ncaab_event.bookmakers:
        for mkt in bk.markets:
            for oc in mkt.outcomes:
                previous.append(
                    {
                        "bookmaker_key": bk.key,
                        "market_key": mkt.key,
                        "outcome_name": oc.name,
                        "outcome_price": oc.price - 10,
                        "outcome_point": oc.point,
                    }
                )

    movement = compute_movement(ncaab_event, previous_bookmaker_odds=previous)

    assert "h2h" in movement, "Expected h2h movement"
    for _team, delta_info in movement["h2h"].items():
        # Current consensus - (current - 10) consensus = +10 expected.
        assert delta_info["price_delta"] == pytest.approx(10.0, abs=1e-6)
        assert "previous_price" in delta_info


# ---------------------------------------------------------------------------
# 10. test_compute_movement_detects_point_change
# ---------------------------------------------------------------------------


def test_compute_movement_detects_point_change(ncaab_event: OddsAPIEvent) -> None:
    """Construct previous spreads with point shifted by +1; expect negative point_delta."""
    previous: list[dict] = []
    for bk in ncaab_event.bookmakers:
        for mkt in bk.markets:
            for oc in mkt.outcomes:
                previous.append(
                    {
                        "bookmaker_key": bk.key,
                        "market_key": mkt.key,
                        "outcome_name": oc.name,
                        "outcome_price": oc.price,
                        "outcome_point": (oc.point + 1.0) if oc.point is not None else None,
                    }
                )

    movement = compute_movement(ncaab_event, previous_bookmaker_odds=previous)

    assert "spreads" in movement, "Expected spreads movement"
    for _team, delta_info in movement["spreads"].items():
        assert "point_delta" in delta_info
        # Current avg point minus (avg point + 1.0) = -1.0
        assert delta_info["point_delta"] == pytest.approx(-1.0, abs=1e-6)
        assert "previous_point" in delta_info


# ---------------------------------------------------------------------------
# 11. test_build_enriched_event_structure
# ---------------------------------------------------------------------------


def test_build_enriched_event_structure(ncaab_event: OddsAPIEvent) -> None:
    snap_id = uuid.uuid4()
    result = build_enriched_event(
        event=ncaab_event,
        snapshot_id=snap_id,
        sport_group="Basketball",
        status="upcoming",
    )

    assert result.event_id == ncaab_event.id
    assert result.sport_key == ncaab_event.sport_key
    assert result.sport_group == "Basketball"
    assert result.home_team == ncaab_event.home_team
    assert result.away_team == ncaab_event.away_team
    assert result.commence_time == ncaab_event.commence_time
    assert result.status == "upcoming"
    assert result.snapshot_id == snap_id
    assert result.fetched_at is not None

    assert result.bookmakers
    assert result.best_line
    assert result.consensus
    assert result.vig_free
    # movement is empty dict on first fetch — that's valid
    assert isinstance(result.movement, dict)

    # All expected markets should be present.
    for market_key in ("h2h", "spreads", "totals"):
        assert market_key in result.best_line, f"'{market_key}' missing from best_line"
        assert market_key in result.consensus, f"'{market_key}' missing from consensus"
        assert market_key in result.vig_free, f"'{market_key}' missing from vig_free"


# ---------------------------------------------------------------------------
# 12. test_build_enriched_event_bookmakers_dict_is_plain_dicts
# ---------------------------------------------------------------------------


def test_build_enriched_event_bookmakers_dict_is_plain_dicts(
    ncaab_event: OddsAPIEvent,
) -> None:
    """Verify that the JSONB-bound data is all plain Python dicts/scalars.

    We check via model_dump() which is what callers use before JSONB insertion.
    The Pydantic model may coerce inner objects on construction, but model_dump()
    must return plain dicts throughout.
    """
    snap_id = uuid.uuid4()
    result = build_enriched_event(
        event=ncaab_event,
        snapshot_id=snap_id,
        sport_group="Basketball",
        status="upcoming",
    )

    dumped = result.model_dump()

    # bookmakers: outer dict values are dicts (market_key -> market dict)
    for bk_key, markets in dumped["bookmakers"].items():
        assert isinstance(markets, dict), (
            f"bookmakers['{bk_key}'] should be a plain dict, got {type(markets)}"
        )
        for market_key, market_val in markets.items():
            assert isinstance(market_val, dict), (
                f"bookmakers['{bk_key}']['{market_key}'] should be a plain dict, "
                f"got {type(market_val)}"
            )

    # best_line: market -> outcome -> dict
    for market_key, outcomes in dumped["best_line"].items():
        assert isinstance(outcomes, dict), (
            f"best_line['{market_key}'] should be a plain dict, got {type(outcomes)}"
        )
        for outcome_name, entry in outcomes.items():
            assert isinstance(entry, dict), (
                f"best_line['{market_key}']['{outcome_name}'] should be a plain dict, "
                f"got {type(entry)}"
            )
            assert isinstance(entry["price"], float | int)

    # consensus: same shape
    for market_key, outcomes in dumped["consensus"].items():
        assert isinstance(outcomes, dict)
        for outcome_name, entry in outcomes.items():
            assert isinstance(entry, dict), (
                f"consensus['{market_key}']['{outcome_name}'] should be a plain dict, "
                f"got {type(entry)}"
            )

    # vig_free: same shape
    for market_key, outcomes in dumped["vig_free"].items():
        assert isinstance(outcomes, dict)
        for outcome_name, entry in outcomes.items():
            assert isinstance(entry, dict), (
                f"vig_free['{market_key}']['{outcome_name}'] should be a plain dict, "
                f"got {type(entry)}"
            )
            assert isinstance(entry["implied_prob"], float | int)
