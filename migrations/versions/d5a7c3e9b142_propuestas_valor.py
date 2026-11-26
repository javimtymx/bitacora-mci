"""propuestas de valor por iniciativa del Círculo (lienzo, épicas, historias y prompt de prototipo); lo que hace
OneCore por módulo (base del benchmark); insights del benchmark enviados a Ideas

Revision ID: d5a7c3e9b142
Revises: c9d2a4e7f310
Create Date: 2026-11-26 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5a7c3e9b142'
down_revision: Union[str, None] = 'c9d2a4e7f310'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    texto_largo = sa.Text().with_variant(sa.Text(length=4294967295), "mysql")
    op.create_table(
        'propuestas_valor',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('iniciativa_id', sa.Integer(), sa.ForeignKey('iniciativas.id', name='fk_pv_iniciativa'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('estado', sa.String(12), nullable=False, server_default='pendiente'),
        sa.Column('etapa', sa.String(20), nullable=False, server_default=''),
        sa.Column('etapas', sa.JSON(), nullable=True),
        sa.Column('evidencia', sa.JSON(), nullable=True),
        sa.Column('perfil', sa.JSON(), nullable=True),
        sa.Column('mapa', sa.JSON(), nullable=True),
        sa.Column('epicas', sa.JSON(), nullable=True),
        sa.Column('prompt_prototipo', texto_largo, nullable=True),
        sa.Column('error', sa.String(500), nullable=False, server_default=''),
        sa.Column('latido', sa.DateTime(), nullable=True),
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_pv_usuario'), nullable=True),
        sa.Column('creado', sa.DateTime(), nullable=False),
        sa.Column('actualizado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_propuestas_valor_iniciativa_id', 'propuestas_valor', ['iniciativa_id'])
    t = op.create_table(
        'modulos_onecore',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('nombre', sa.String(80), nullable=False),
        sa.Column('resumen', sa.String(300), nullable=False, server_default=''),
        *[sa.Column(c, sa.Text(), nullable=True) for c in ('para_quien', 'proceso', 'funciones', 'integraciones',
                                                           'diferenciadores', 'no_hace', 'en_desarrollo')],
        sa.Column('orden', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('historial', sa.JSON(), nullable=True),
        sa.Column('actualizado', sa.DateTime(), nullable=False),
        sa.Column('actualizado_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_modonecore_usuario'), nullable=True),
        sa.UniqueConstraint('nombre', name='uq_modulos_onecore_nombre'),
    )
    from datetime import datetime, timezone
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    base = [("Smart Files", "expediente documental de comercio exterior"),
            ("SAFE", "validación de pedimentos antes del pago"),
            ("Manifestación de Valor", "MVE y respaldo de valor en aduana"),
            ("CIVA", "certificación IVA/IEPS y su control"),
            ("Activos Fijos", "control de activo fijo IMMEX"),
            ("Reconstrucción", "reconstrucción de operaciones y descargos"),
            ("Análisis 360", "informe mensual de cumplimiento del cliente")]
    op.bulk_insert(t, [{"nombre": n, "resumen": d, "orden": k, "activo": True, "historial": [], "actualizado": ahora,
                        "para_quien": "", "proceso": "", "funciones": "", "integraciones": "", "diferenciadores": "",
                        "no_hace": "", "en_desarrollo": ""} for k, (n, d) in enumerate(base, 1)])
    with op.batch_alter_table('insights_benchmark') as b:
        b.add_column(sa.Column('idea_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_insight_idea', 'ideas', ['idea_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('insights_benchmark') as b:
        b.drop_constraint('fk_insight_idea', type_='foreignkey')
        b.drop_column('idea_id')
    op.drop_table('modulos_onecore')
    op.drop_index('ix_propuestas_valor_iniciativa_id', table_name='propuestas_valor')
    op.drop_table('propuestas_valor')
