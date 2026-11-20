"""activate the Red Team Sandbox Agent

The Red Team Lab used to create its sandbox agent as "inactive", so it could not be run from the Agents page.
New sandboxes are now created active (the Orchestrator still never routes to it: it only delegates to its listed
collaborators). This one-time data migration brings an existing inactive sandbox in line. It runs once; an admin
can still deactivate the agent afterwards.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NAME = "Red Team Sandbox Agent"
DESCRIPTION = ("Sandbox used by the Red Team Lab to probe prompt-injection and secret-leak defenses. "
               "The Orchestrator never routes to it; run it directly with an attack prompt.")


agents = sa.table("agents", sa.column("name", sa.String), sa.column("status", sa.String),
                  sa.column("description", sa.String))


def upgrade() -> None:
    op.execute(agents.update().where(agents.c.name == NAME, agents.c.status == "inactive")
               .values(status="active", description=DESCRIPTION))


def downgrade() -> None:
    op.execute(agents.update().where(agents.c.name == NAME, agents.c.status == "active").values(status="inactive"))
