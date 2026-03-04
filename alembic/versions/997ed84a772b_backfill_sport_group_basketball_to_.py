"""backfill_sport_group_basketball_to_league

Revision ID: 997ed84a772b
Revises: 218ef530bb5e
Create Date: 2026-03-04 14:39:52.936304

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '997ed84a772b'
down_revision: str | Sequence[str] | None = '218ef530bb5e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE events SET sport_group = 'NCAAB' WHERE sport_key LIKE 'basketball_ncaab%'")
    op.execute("UPDATE events SET sport_group = 'NBA' WHERE sport_key LIKE 'basketball_nba%'")


def downgrade() -> None:
    op.execute("UPDATE events SET sport_group = 'Basketball' WHERE sport_key LIKE 'basketball_%'")
