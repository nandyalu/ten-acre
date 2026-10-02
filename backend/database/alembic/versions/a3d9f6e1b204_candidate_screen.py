"""candidate screen

``candidatescreen`` — every name a candidate screen returned, and when.
Experiment 2's random control books draw their tickers from it (PLAN.md).
Empty before 2026-10-02. Nothing is backfilled.

Revision ID: a3d9f6e1b204
Revises: e7b4c2a9d150
Create Date: 2026-10-02 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a3d9f6e1b204"
down_revision: Union[str, Sequence[str], None] = "e7b4c2a9d150"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    if "candidatescreen" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "candidatescreen",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("screened_at", sa.DateTime(), nullable=False),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
    )
    op.create_index("ix_candidatescreen_screened_at", "candidatescreen", ["screened_at"])
    op.create_index("ix_candidatescreen_ticker", "candidatescreen", ["ticker"])

def downgrade() -> None:
    op.drop_table("candidatescreen")
