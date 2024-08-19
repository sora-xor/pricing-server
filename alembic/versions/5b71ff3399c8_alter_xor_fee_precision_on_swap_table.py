"""alter xor_fee precision on swap table

Revision ID: 5b71ff3399c8
Revises: 0d55c1ee6b9a
Create Date: 2024-08-19 15:44:43.155055

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5b71ff3399c8'
down_revision = '0d55c1ee6b9a'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('swap', 'xor_fee', type_=sa.Numeric(precision=40), nullable=False)


def downgrade():
    op.alter_column('swap', 'xor_fee', type_=sa.Numeric(precision=20), nullable=False)
