"""add workspace branding columns

Revision ID: 001
Revises: 
Create Date: 2025-11-17

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add workspace branding columns
    with op.batch_alter_table('workspace') as batch_op:
        batch_op.add_column(sa.Column('site_title', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('logo_url', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('favicon_url', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('primary_color', sa.String(), nullable=True, server_default='#2563eb'))


def downgrade():
    # Remove workspace branding columns
    with op.batch_alter_table('workspace') as batch_op:
        batch_op.drop_column('primary_color')
        batch_op.drop_column('favicon_url')
        batch_op.drop_column('logo_url')
        batch_op.drop_column('site_title')
