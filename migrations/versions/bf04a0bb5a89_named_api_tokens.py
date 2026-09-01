"""named api tokens

Moves API tokens out of the users table into their own, so a user can hold
several — one per integration — each with a name and its own revocation.

Any existing token is carried across rather than reissued: it is live and in use
by an external client, and dropping it would silently break that integration.

Revision ID: bf04a0bb5a89
Revises: bcc497dee9ef
Create Date: 2026-09-01

"""
from alembic import op
import sqlalchemy as sa


revision = 'bf04a0bb5a89'
down_revision = 'bcc497dee9ef'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'api_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name='fk_api_tokens_user_id_users', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('api_tokens', schema=None) as batch_op:
        batch_op.create_index('ix_api_tokens_user_id', ['user_id'], unique=False)
        batch_op.create_index('ix_api_tokens_token_hash', ['token_hash'], unique=True)

    # Carry existing tokens over so live integrations keep working.
    op.execute(
        "INSERT INTO api_tokens (user_id, name, token_hash, created_at, last_used_at) "
        "SELECT id, 'Existing token', api_token_hash, api_token_created, api_token_last_used "
        "FROM users WHERE api_token_hash IS NOT NULL"
    )

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index('ix_users_api_token_hash')
        batch_op.drop_column('api_token_last_used')
        batch_op.drop_column('api_token_created')
        batch_op.drop_column('api_token_hash')


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('api_token_hash', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('api_token_created', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('api_token_last_used', sa.DateTime(), nullable=True))
        batch_op.create_index('ix_users_api_token_hash', ['api_token_hash'], unique=False)

    # Only one token per user fits the old shape; keep the most recently created.
    op.execute(
        "UPDATE users SET api_token_hash = ("
        "  SELECT token_hash FROM api_tokens WHERE api_tokens.user_id = users.id "
        "  ORDER BY created_at DESC LIMIT 1)"
    )

    with op.batch_alter_table('api_tokens', schema=None) as batch_op:
        batch_op.drop_index('ix_api_tokens_token_hash')
        batch_op.drop_index('ix_api_tokens_user_id')
    op.drop_table('api_tokens')
