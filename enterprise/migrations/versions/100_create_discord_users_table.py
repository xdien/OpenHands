"""create discord_users table

Revision ID: 100
Revises: 099_create_user_authorizations_table
Create Date: 2026-03-11

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '100'
down_revision = '099'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'discord_users',
        sa.Column('id', sa.Integer(), sa.Identity(), nullable=False),
        sa.Column('keycloak_user_id', sa.String(), nullable=True),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('discord_user_id', sa.String(), nullable=False),
        sa.Column('discord_username', sa.String(), nullable=False),
        sa.Column('discord_discriminator', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['org.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_discord_users_keycloak_user_id', 'discord_users', ['keycloak_user_id'])
    op.create_index('ix_discord_users_discord_user_id', 'discord_users', ['discord_user_id'])


def downgrade() -> None:
    op.drop_index('ix_discord_users_discord_user_id', table_name='discord_users')
    op.drop_index('ix_discord_users_keycloak_user_id', table_name='discord_users')
    op.drop_table('discord_users')
