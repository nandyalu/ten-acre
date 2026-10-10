"""sim order

``simorder`` — the state of the in-process simulated broker, ``BROKER=sim``.

Experiment 2 trades through it (PLAN.md). A deployment on Webull or Alpaca
never writes a row.

Revision ID: e7b4c2a9d150
Revises: d5a2e8c14f37
Create Date: 2026-10-02 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e7b4c2a9d150"
down_revision: Union[str, Sequence[str], None] = "d5a2e8c14f37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    if "simorder" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "simorder",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_order_id", sa.String(), nullable=False),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("order_type", sa.String(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("time_in_force", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("group_id", sa.String(), nullable=True),
        sa.Column("placed_at", sa.DateTime(), nullable=False),
        sa.Column("checked_through", sa.DateTime(), nullable=False),
        sa.Column("filled_at", sa.DateTime(), nullable=True),
        sa.Column("filled_price", sa.Float(), nullable=True),
        sa.Column("filled_quantity", sa.Float(), nullable=True),
    )
    op.create_index("ix_simorder_client_order_id", "simorder", ["client_order_id"], unique=True)
    op.create_index("ix_simorder_ticker", "simorder", ["ticker"])
    op.create_index("ix_simorder_status", "simorder", ["status"])
    op.create_index("ix_simorder_parent_id", "simorder", ["parent_id"])
    op.create_index("ix_simorder_group_id", "simorder", ["group_id"])

def downgrade() -> None:
    op.drop_table("simorder")
