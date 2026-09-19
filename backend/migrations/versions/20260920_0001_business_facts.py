"""Create stage 02A authoritative business facts.

Revision ID: 20260920_0001
Revises: None
"""

from alembic import op

from app.business.models import Base

revision = "20260920_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The teaching_business schema is deployment-owned and must already exist.
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Drop only business metadata. The langgraph_checkpoint schema is never referenced.
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
