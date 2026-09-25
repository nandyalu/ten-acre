"""agent reflection

``agentreflection`` — the agent's evening review of its own day (2026-09-24).

Once a trading day, after the close and the grading, the agent reads every
pass since its last review and answers two forced function calls: notes to
the maintainer, then a revision of the note for the next pass and of its
memory notes. Its own table, because a review is not a pass: ``agentrun``
feeds "your recent wakeups" and the idle-pass count, and a review must count
in neither. See the 2026-09-24 entries in JOURNEY.md.

Revision ID: b7e3c5d9a1f4
Revises: c3e81f40a7d2
Create Date: 2026-09-24 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = "b7e3c5d9a1f4"
down_revision: Union[str, Sequence[str], None] = "c3e81f40a7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "agentreflection" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "agentreflection",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ran_at", sa.DateTime(), nullable=False),
        sa.Column("since", sa.DateTime(), nullable=False),
        sa.Column("passes", sa.Integer(), nullable=False),
        sa.Column("prompt", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("turn2_prompt", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("response", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("revision", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("thinking", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("wakeup_note", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("memory_changes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("applied", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("channel", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("seconds", sa.Float(), nullable=True),
        sa.Column("skipped", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agentreflection_ran_at"), "agentreflection", ["ran_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agentreflection_ran_at"), table_name="agentreflection")
    op.drop_table("agentreflection")
