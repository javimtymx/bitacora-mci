"""idea ligada a la sesion de voz del cliente de donde nacio

Revision ID: c6a2e9f41b8d
Revises: b4e8a1c2d7f9
Create Date: 2026-11-03 19:55:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c6a2e9f41b8d'
down_revision: Union[str, None] = 'b4e8a1c2d7f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('ideas') as b:
        b.add_column(sa.Column('sesion_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_ideas_sesion', 'sesiones_cliente', ['sesion_id'], ['id'])
    # ideas que ya se enviaron desde un hallazgo: se ligan por la cita textual que llevan en el problema
    con = op.get_bind()
    hall = con.execute(sa.text("SELECT id, sesion_id, cita FROM hallazgos")).fetchall()
    ideas = con.execute(sa.text("SELECT id, problema FROM ideas WHERE problema LIKE 'Voz del cliente%'")).fetchall()
    for iid, problema in ideas:
        for _hid, sid, cita in hall:
            if cita and cita in (problema or ""):
                con.execute(sa.text("UPDATE ideas SET sesion_id = :s WHERE id = :i"), {"s": sid, "i": iid})
                break


def downgrade() -> None:
    with op.batch_alter_table('ideas') as b:
        b.drop_constraint('fk_ideas_sesion', type_='foreignkey')
        b.drop_column('sesion_id')
