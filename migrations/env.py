"""Alembic environment: URL from -x/config or DATABASE_URL. Migrations are explicit SQL."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

config = context.config
url = config.get_main_option("sqlalchemy.url") or os.environ["DATABASE_URL"]

connectable = create_engine(url)
with connectable.connect() as connection:
    context.configure(connection=connection, target_metadata=None, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()
connectable.dispose()
