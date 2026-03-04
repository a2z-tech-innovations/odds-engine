"""backfill_sport_group_tennis_to_atp_wta

Revision ID: aa04ff78e30f
Revises: 997ed84a772b
Create Date: 2026-03-04 14:54:20.619936

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'aa04ff78e30f'
down_revision: str | Sequence[str] | None = '997ed84a772b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE events SET sport_group = 'ATP' WHERE sport_key LIKE 'tennis_atp_%'")
    op.execute("UPDATE events SET sport_group = 'WTA' WHERE sport_key LIKE 'tennis_wta_%'")


def downgrade() -> None:
    op.execute("UPDATE events SET sport_group = 'Tennis' WHERE sport_key LIKE 'tennis_%'")
