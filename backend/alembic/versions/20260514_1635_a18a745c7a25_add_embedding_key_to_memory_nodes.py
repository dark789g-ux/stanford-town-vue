"""add embedding_key to memory_nodes

Revision ID: a18a745c7a25
Revises: d487ca4d1053
Create Date: 2026-05-14 16:35:29.115704+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a18a745c7a25'
down_revision: Union[str, None] = 'd487ca4d1053'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ``embedding_key`` holds a memory node's canonical natural-language text
    # (what the simulator feeds into reflection/planning/conversation prompts
    # and indexes in embeddings.json). The on-disk format stores it separately
    # from ``description`` (which is empty for bootstrap-style nodes), so it
    # needs its own column to survive a DB round-trip. Existing rows predate
    # this column and have no source text to recover — they default to "".
    with op.batch_alter_table("memory_nodes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "embedding_key",
                sa.Text(),
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("memory_nodes", schema=None) as batch_op:
        batch_op.drop_column("embedding_key")
