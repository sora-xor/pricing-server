"""Added liquidity to pairs

Revision ID: a93bf2ae653f
Revises: 5b71ff3399c8
Create Date: 2024-12-24 17:24:19.293241

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a93bf2ae653f'
down_revision = '5b71ff3399c8'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('pair', sa.Column('from_token_liquidity', sa.Numeric(), nullable=True))
    op.add_column('pair', sa.Column('to_token_liquidity', sa.Numeric(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('pair', 'to_token_liquidity')
    op.drop_column('pair', 'from_token_liquidity')
    # ### end Alembic commands ###
