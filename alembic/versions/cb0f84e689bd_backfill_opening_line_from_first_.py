"""backfill opening_line from first enriched snapshot

Revision ID: cb0f84e689bd
Revises: 800a6a0a903d
Create Date: 2026-03-05 00:07:23.377127

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = 'cb0f84e689bd'
down_revision: str | Sequence[str] | None = '800a6a0a903d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill opening_line from the earliest enriched_snapshot per event."""
    from alembic import op

    op.execute("""
        UPDATE events e
        SET opening_line = es.best_line
        FROM enriched_snapshots es
        WHERE e.opening_line = '{}'::jsonb
          AND es.id = (
              SELECT id FROM enriched_snapshots
              WHERE event_id = e.id
              ORDER BY computed_at ASC
              LIMIT 1
          )
    """)


def downgrade() -> None:
    """Cannot reverse — would need to know which rows were backfilled."""
    pass
