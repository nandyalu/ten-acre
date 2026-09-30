"""agent run unanswered

``agentrun.unanswered`` — true when the model never answered the pass.

On 2026-09-28 Google answered eight passes in a row with 503, and each pass
raised before it wrote a row. The record showed no pass at all for that half
hour. Such a pass now writes a row with ``skipped`` set and this flag true, and
the readers of "the previous pass" skip it.

False on every row before 2026-09-28. Nothing is backfilled.

Revision ID: d5a2e8c14f37
Revises: b7e3c5d9a1f4
Create Date: 2026-09-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d5a2e8c14f37"
down_revision: Union[str, Sequence[str], None] = "b7e3c5d9a1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agentrun")}
    if "unanswered" not in existing:
        op.add_column(
            "agentrun",
            sa.Column("unanswered", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

def downgrade() -> None:
    op.drop_column("agentrun", "unanswered")
