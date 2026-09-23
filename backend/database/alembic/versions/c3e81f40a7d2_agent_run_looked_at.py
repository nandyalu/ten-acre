"""agent run looked at

``agentrun.looked_at`` — when the pass built its last prompt.

"What was noticed since your last pass" started at ``ran_at``, which is when
the pass was recorded. A pass can run for many minutes after its last prompt,
so an alert raised in that time never reached any prompt. On 2026-09-23 a stop
filled during a pass and the next pass could not have seen it.

NULL on a skipped pass and on every row before 2026-09-23. Readers use
``ran_at`` then. Nothing is backfilled.

Revision ID: c3e81f40a7d2
Revises: a9c4e17b52f0
Create Date: 2026-09-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c3e81f40a7d2"
down_revision: Union[str, Sequence[str], None] = "a9c4e17b52f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agentrun")}
    if "looked_at" not in existing:
        op.add_column("agentrun", sa.Column("looked_at", sa.DateTime(), nullable=True))

def downgrade() -> None:
    op.drop_column("agentrun", "looked_at")
