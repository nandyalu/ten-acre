"""system prompt and corporate action

``systemprompt`` — each distinct system message a decision turn was sent,
once; a turn keeps only its hash. ``corporateaction`` — splits and spin-offs,
and when each was applied to this deployment's prices, ledger and orders.

Revision ID: b6e1c4d8f372
Revises: a3d9f6e1b204
Create Date: 2026-10-02 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b6e1c4d8f372"
down_revision: Union[str, Sequence[str], None] = "a3d9f6e1b204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "systemprompt" not in tables:
        op.create_table(
            "systemprompt",
            sa.Column("sha", sa.String(), primary_key=True),
            sa.Column("text", sa.String(), nullable=False),
            sa.Column("first_seen", sa.DateTime(), nullable=False),
        )
    if "corporateaction" not in tables:
        op.create_table(
            "corporateaction",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("ex_date", sa.Date(), nullable=False),
            sa.Column("ratio", sa.Float(), nullable=False),
            sa.Column("child", sa.String(), nullable=True),
            sa.Column("price_factor", sa.Float(), nullable=True),
            sa.Column("applied_at", sa.DateTime(), nullable=True),
            sa.Column("note", sa.String(), nullable=True),
        )
        op.create_index("ix_corporateaction_ticker", "corporateaction", ["ticker"])
        op.create_index("ix_corporateaction_ex_date", "corporateaction", ["ex_date"])

def downgrade() -> None:
    op.drop_table("corporateaction")
    op.drop_table("systemprompt")
