class OddsEngineError(Exception):
    """Base exception for all odds engine domain errors."""


class BudgetExhaustedError(OddsEngineError):
    """Raised when the API credit budget is exhausted."""


class EventNotFoundError(OddsEngineError):
    """Raised when a requested event does not exist."""

    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        super().__init__(f"Event not found: {event_id}")


class OddsAPIError(OddsEngineError):
    """Raised when The Odds API returns an error response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Odds API error {status_code}: {message}")


class StaleDataError(OddsEngineError):
    """Raised when cached data is too old to be reliable."""
