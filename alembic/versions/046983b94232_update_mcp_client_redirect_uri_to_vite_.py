"""update mcp client redirect_uri to vite 5173

Revision ID: 046983b94232
Revises: 04bfd6c86a64
Create Date: 2026-07-15 16:00:17.545553

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '046983b94232'
down_revision: Union[str, Sequence[str], None] = '04bfd6c86a64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE oauth_clients
        SET redirect_uris = '["http://localhost:5173/callback"]'::jsonb
        WHERE client_id = 'wakilni-mcp'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE oauth_clients
        SET redirect_uris = '["http://localhost:8080/callback"]'::jsonb
        WHERE client_id = 'wakilni-mcp'
        """
    )
