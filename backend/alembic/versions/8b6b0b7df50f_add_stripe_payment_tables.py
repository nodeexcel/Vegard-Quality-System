"""add_stripe_payment_tables

Revision ID: 8b6b0b7df50f
Revises: a120f6de02f4
Create Date: 2025-12-17 06:09:24.701474

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8b6b0b7df50f'
down_revision = 'a120f6de02f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create stripe_customers table
    op.create_table('stripe_customers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('stripe_customer_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
        sa.UniqueConstraint('stripe_customer_id')
    )
    op.create_index(op.f('ix_stripe_customers_id'), 'stripe_customers', ['id'], unique=False)
    op.create_index(op.f('ix_stripe_customers_stripe_customer_id'), 'stripe_customers', ['stripe_customer_id'], unique=True)
    
    # Create credit_packages table
    op.create_table('credit_packages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('credits_amount', sa.Integer(), nullable=False),
        sa.Column('price_nok', sa.Integer(), nullable=False),
        sa.Column('stripe_price_id', sa.String(), nullable=True),
        sa.Column('is_active', sa.Integer(), server_default='1', nullable=False),
        sa.Column('display_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_credit_packages_id'), 'credit_packages', ['id'], unique=False)
    
    # Create stripe_payments table
    op.create_table('stripe_payments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('stripe_payment_intent_id', sa.String(), nullable=False),
        sa.Column('stripe_customer_id', sa.String(), nullable=True),
        sa.Column('amount_nok', sa.Integer(), nullable=False),
        sa.Column('credits_purchased', sa.Integer(), nullable=False),
        sa.Column('credit_package_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['credit_package_id'], ['credit_packages.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stripe_payment_intent_id')
    )
    op.create_index(op.f('ix_stripe_payments_id'), 'stripe_payments', ['id'], unique=False)
    op.create_index(op.f('ix_stripe_payments_stripe_payment_intent_id'), 'stripe_payments', ['stripe_payment_intent_id'], unique=True)


def downgrade() -> None:
    # Drop indexes and tables in reverse order
    op.drop_index(op.f('ix_stripe_payments_stripe_payment_intent_id'), table_name='stripe_payments')
    op.drop_index(op.f('ix_stripe_payments_id'), table_name='stripe_payments')
    op.drop_table('stripe_payments')
    
    op.drop_index(op.f('ix_credit_packages_id'), table_name='credit_packages')
    op.drop_table('credit_packages')
    
    op.drop_index(op.f('ix_stripe_customers_stripe_customer_id'), table_name='stripe_customers')
    op.drop_index(op.f('ix_stripe_customers_id'), table_name='stripe_customers')
    op.drop_table('stripe_customers')

