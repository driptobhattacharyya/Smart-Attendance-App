"""SQL builders for migrations: row level security + grants for a tenant table.

Migrations import this; keep it backward compatible. Every new tenant table (step 2 onwards)
must be passed through ``tenant_table_statements`` in the migration that creates it.
"""

from __future__ import annotations

APP_ROLE = "attendify_app"
SYSTEM_ROLE = "attendify_system"

READ_WRITE = "SELECT, INSERT, UPDATE, DELETE"


def tenant_table_statements(
    table: str, *, org_col: str = "org_id", app_privileges: str = READ_WRITE
) -> list[str]:
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING ({org_col} = app_org_id()) WITH CHECK ({org_col} = app_org_id())",
        f"GRANT {app_privileges} ON {table} TO {APP_ROLE}",
        f"GRANT ALL ON {table} TO {SYSTEM_ROLE}",
    ]


def updated_at_trigger_statements(table: str) -> list[str]:
    return [
        f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    ]
