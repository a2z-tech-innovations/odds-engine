"""Pure enrichment functions for odds data.

Stateless module — no I/O, no DB, no Redis. All functions accept Pydantic
schemas and return plain Python dicts (safe for JSONB storage).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from odds_engine.schemas.enriched import EnrichedEventResponse

if TYPE_CHECKING:
    import uuid

    from odds_engine.schemas.odds_api import OddsAPIEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _american_to_raw_prob(price: float) -> float:
    """Convert American odds to raw implied probability."""
    if price > 0:
        return 100.0 / (price + 100.0)
    return abs(price) / (abs(price) + 100.0)


# ---------------------------------------------------------------------------
# Core enrichment functions
# ---------------------------------------------------------------------------


def compute_best_line(event: OddsAPIEvent) -> dict:
    """Return the best (highest) price per market per outcome across all bookmakers.

    Higher American odds = better for the bettor (more payout), so we take max.

    Returns plain dicts — safe for JSONB storage.
    """
    # Structure: {market_key: {outcome_name: {price, bookmaker, point}}}
    best: dict[str, dict[str, dict]] = defaultdict(dict)

    for bookmaker in event.bookmakers:
        for market in bookmaker.markets:
            for outcome in market.outcomes:
                current = best[market.key].get(outcome.name)
                if current is None or outcome.price > current["price"]:
                    entry: dict = {
                        "price": outcome.price,
                        "bookmaker": bookmaker.key,
                    }
                    if market.key in ("spreads", "totals") and outcome.point is not None:
                        entry["point"] = outcome.point
                    best[market.key][outcome.name] = entry

    return {market_key: dict(outcomes) for market_key, outcomes in best.items()}


def compute_consensus(event: OddsAPIEvent) -> dict:
    """Return arithmetic mean of prices across bookmakers per market per outcome.

    Only bookmakers that carry the given market are included in the average.

    Returns plain dicts — safe for JSONB storage.
    """
    # Accumulate: {market_key: {outcome_name: [prices]}}
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for bookmaker in event.bookmakers:
        for market in bookmaker.markets:
            for outcome in market.outcomes:
                acc[market.key][outcome.name].append(outcome.price)

    result: dict[str, dict[str, dict]] = {}
    for market_key, outcomes in acc.items():
        result[market_key] = {
            name: {"price": sum(prices) / len(prices)}
            for name, prices in outcomes.items()
        }
    return result


def compute_vig_free(event: OddsAPIEvent) -> dict:
    """Compute vig-free implied probabilities from consensus prices.

    Algorithm per market:
    1. Compute consensus price for each outcome.
    2. Convert each price to raw implied probability.
    3. Normalise by dividing each raw prob by the total (removes vig).
    4. Round to 4 decimal places.

    Returns plain dicts — safe for JSONB storage.
    """
    consensus = compute_consensus(event)
    result: dict[str, dict[str, dict]] = {}

    for market_key, outcomes in consensus.items():
        raw_probs = {
            name: _american_to_raw_prob(data["price"]) for name, data in outcomes.items()
        }
        total = sum(raw_probs.values())
        if total == 0:
            continue
        result[market_key] = {
            name: {"implied_prob": round(raw / total, 4)} for name, raw in raw_probs.items()
        }

    return result


def compute_movement(
    event: OddsAPIEvent,
    previous_bookmaker_odds: list[dict] | None,
) -> dict:
    """Compare the current snapshot to a previous one and return deltas.

    ``previous_bookmaker_odds`` is a list of repository row dicts with keys:
    ``bookmaker_key``, ``market_key``, ``outcome_name``, ``outcome_price``,
    ``outcome_point``.

    Movement is computed relative to the **previous consensus** (mean over all
    books in the previous snapshot) compared with the **current consensus**.

    Returns an empty dict when no previous data is available.
    Returns plain dicts — safe for JSONB storage.
    """
    if not previous_bookmaker_odds:
        return {}

    current_consensus = compute_consensus(event)

    # Build previous consensus from the raw rows.
    prev_acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    prev_point_acc: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for row in previous_bookmaker_odds:
        mkey = row["market_key"]
        oname = row["outcome_name"]
        prev_acc[mkey][oname].append(float(row["outcome_price"]))
        if row.get("outcome_point") is not None:
            prev_point_acc[mkey][oname].append(float(row["outcome_point"]))

    prev_consensus: dict[str, dict[str, dict]] = {}
    for mkey, outcomes in prev_acc.items():
        prev_consensus[mkey] = {}
        for oname, prices in outcomes.items():
            entry: dict = {"price": sum(prices) / len(prices)}
            if oname in prev_point_acc.get(mkey, {}):
                pts = prev_point_acc[mkey][oname]
                entry["point"] = sum(pts) / len(pts)
            prev_consensus[mkey][oname] = entry

    result: dict[str, dict[str, dict]] = {}

    for market_key, outcomes in current_consensus.items():
        if market_key not in prev_consensus:
            continue
        market_result: dict[str, dict] = {}
        for outcome_name, current_data in outcomes.items():
            prev_data = prev_consensus[market_key].get(outcome_name)
            if prev_data is None:
                continue
            current_price = current_data["price"]
            prev_price = prev_data["price"]
            movement: dict = {
                "price_delta": current_price - prev_price,
                "previous_price": prev_price,
            }
            if "point" in prev_data:
                # Current point — take from best_line or consensus accumulator
                curr_points: list[float] = []
                for bookmaker in event.bookmakers:
                    for mkt in bookmaker.markets:
                        if mkt.key != market_key:
                            continue
                        for oc in mkt.outcomes:
                            if oc.name == outcome_name and oc.point is not None:
                                curr_points.append(oc.point)
                current_point = (
                    sum(curr_points) / len(curr_points) if curr_points else None
                )
                prev_point = prev_data["point"]
                movement["point_delta"] = (
                    (current_point - prev_point) if current_point is not None else None
                )
                movement["previous_point"] = prev_point
            market_result[outcome_name] = movement
        if market_result:
            result[market_key] = market_result

    return result


def build_enriched_event(
    event: OddsAPIEvent,
    snapshot_id: uuid.UUID,
    sport_group: str,
    status: str,
    previous_bookmaker_odds: list[dict] | None = None,
) -> EnrichedEventResponse:
    """Orchestrate the full enrichment pipeline for a single event.

    Steps:
    1. compute_best_line
    2. compute_consensus
    3. compute_vig_free
    4. compute_movement
    5. Build bookmakers plain-dict structure
    6. Construct and return EnrichedEventResponse

    All nested dicts are plain Python dicts — no Pydantic model instances —
    so they are safe for JSONB storage.
    """
    best_line = compute_best_line(event)
    consensus = compute_consensus(event)
    vig_free = compute_vig_free(event)
    movement = compute_movement(event, previous_bookmaker_odds)

    # Build the consumer-ready bookmakers dict (plain dicts).
    bookmakers_dict: dict[str, dict[str, dict]] = {}
    for bookmaker in event.bookmakers:
        bk_markets: dict[str, dict] = {}
        for market in bookmaker.markets:
            outcomes_list = [
                {
                    "name": oc.name,
                    "price": oc.price,
                    "point": oc.point,
                }
                for oc in market.outcomes
            ]
            bk_markets[market.key] = {
                "outcomes": outcomes_list,
                "last_update": (
                    market.last_update.isoformat() if market.last_update else None
                ),
            }
        bookmakers_dict[bookmaker.key] = bk_markets

    fetched_at = datetime.now(tz=UTC)

    return EnrichedEventResponse(
        event_id=event.id,
        sport_key=event.sport_key,
        sport_group=sport_group,
        home_team=event.home_team,
        away_team=event.away_team,
        commence_time=event.commence_time,
        status=status,
        snapshot_id=snapshot_id,
        fetched_at=fetched_at,
        bookmakers=bookmakers_dict,  # type: ignore[arg-type]
        best_line=best_line,  # type: ignore[arg-type]
        consensus=consensus,  # type: ignore[arg-type]
        vig_free=vig_free,  # type: ignore[arg-type]
        movement=movement,  # type: ignore[arg-type]
    )
