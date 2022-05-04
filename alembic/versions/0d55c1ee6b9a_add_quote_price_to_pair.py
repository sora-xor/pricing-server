"""add quote_price to Pair

Revision ID: 0d55c1ee6b9a
Revises: 4957211c454a
Create Date: 2022-05-04 11:48:57.419989

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0d55c1ee6b9a'
down_revision = '4957211c454a'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('pair', sa.Column('quote_price', sa.Numeric(), nullable=True))


def downgrade():
    op.drop_column('pair', 'quote_price')
