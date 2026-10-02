"""market fetch

``marketfetch`` — for each day, how many fetches of each kind the market
container answered, and how many the book fetched itself. Experiment 2 only.
Empty before 2026-10-03. Nothing is backfilled.

Revision ID: c4f2a8e9d613
Revises: b6e1c4d8f372
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c4f2a8e9d613"
down_revision: Union[str, Sequence[str], None] = "b6e1c4d8f372"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    if "marketfetch" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "marketfetch",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("kind", sa.String(), primary_key=True),
        sa.Column("source", sa.String(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )

def downgrade() -> None:
    op.drop_table("marketfetch")
