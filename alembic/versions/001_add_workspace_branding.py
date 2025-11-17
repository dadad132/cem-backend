"""add workspace branding columns

Revision ID: 001
Revises: 
Create Date: 2025-11-17

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Get connection and check existing columns
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('workspace')]
    
    # Add workspace branding columns only if they don't exist
    with op.batch_alter_table('workspace') as batch_op:
        if 'site_title' not in existing_columns:
            batch_op.add_column(sa.Column('site_title', sa.String(), nullable=True))
        if 'logo_url' not in existing_columns:
            batch_op.add_column(sa.Column('logo_url', sa.String(), nullable=True))
        if 'favicon_url' not in existing_columns:
            batch_op.add_column(sa.Column('favicon_url', sa.String(), nullable=True))
        if 'primary_color' not in existing_columns:
            batch_op.add_column(sa.Column('primary_color', sa.String(), nullable=True, server_default='#2563eb'))


def downgrade():
    # Remove workspace branding columns
    with op.batch_alter_table('workspace') as batch_op:
        batch_op.drop_column('primary_color')
        batch_op.drop_column('favicon_url')
        batch_op.drop_column('logo_url')
        batch_op.drop_column('site_title')
