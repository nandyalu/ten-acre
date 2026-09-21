"""agent run woke because

``agentrun.woke_because`` — what started the pass, in the agent's own prompt.

Four different things start a pass and they call for different answers: the
agent's own chosen time, a move it slept through, the last call before the
close, a change to the app. The agent has been told which since 2026-09-12,
and the record was not. So the Decisions page could show what the agent did and
never why it was asked, and a reader had to open the raw prompt and find the
sentence by eye.

It is one of the eight sentences in ``scheduler._WOKE_BECAUSE``, stored as the
agent was shown it rather than as a label, because the sentence is the thing
that shaped the answer and the labels have been reworded before.

NULL on a skipped pass, which never reaches the model, and on every row before
2026-09-21. Nothing is backfilled. The sentence does survive inside each stored
prompt, but recovering it would mean a second parser reading prose this app
wrote, and a row that parsed wrong would be indistinguishable from a row that
was right.

Revision ID: a9c4e17b52f0
Revises: e4b7d92f1a05
Create Date: 2026-09-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a9c4e17b52f0"
down_revision: Union[str, Sequence[str], None] = "e4b7d92f1a05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agentrun")}
    if "woke_because" not in existing:
        op.add_column("agentrun", sa.Column("woke_because", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agentrun", "woke_because")
