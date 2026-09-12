"""agent run wakeup note

``agentrun.wakeup_note`` — what the agent said it wanted to be woken *for*.

**The agent has no memory between passes.** The only thing that crosses from
one pass to the next is what the prompt carries, and until now that did not
include why the agent had chosen the time it chose. It could set a wakeup to
see whether a price held a level and arrive with no record of what it meant to
check — the wakeup fired, and the reason for it was gone.

It is the agent's own words, from ``next_wakeup_reason`` in its answer, shown
back to it on the pass that wakeup starts. Capped in the prompt rather than
here, so a long one is stored whole and trimmed when shown.

NULL means the agent named no reason, which is different from naming an empty
one, and NULL on every row before 2026-09-12. Nothing is backfilled: those
passes really did leave no note, and inventing one would be writing a record
nobody kept.

Revision ID: e4b7d92f1a05
Revises: d1f4a63c85b2
Create Date: 2026-09-12 06:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e4b7d92f1a05"
down_revision: Union[str, Sequence[str], None] = "d1f4a63c85b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agentrun")}
    if "wakeup_note" not in existing:
        op.add_column("agentrun", sa.Column("wakeup_note", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agentrun", "wakeup_note")
