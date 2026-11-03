"""import v2: sessions, profiles, undo tracking

Revision ID: f3a9c2e1b7d4
Revises: d8f1b3a5c7e2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f3a9c2e1b7d4"
down_revision = "d8f1b3a5c7e2"
branch_labels = None
depends_on = None

IMPORT_STATUS = postgresql.ENUM(
    "draft", "completed", "failed", "reverted", name="importstatus"
)


def upgrade() -> None:
    IMPORT_STATUS.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "import",
        sa.Column(
            "status",
            postgresql.ENUM(name="importstatus", create_type=False),
            nullable=False,
            server_default="completed",
        ),
    )
    op.create_index("ix_import_status", "import", ["status"])
    op.create_index("ix_import_owner_id", "import", ["owner_id"])
    op.alter_column("import", "file_content", existing_type=sa.String(), nullable=True)
    op.add_column("import", sa.Column("filename", sa.String(length=255)))
    op.add_column("import", sa.Column("file_hash", sa.String(length=64)))
    op.create_index("ix_import_file_hash", "import", ["file_hash"])
    op.add_column("import", sa.Column("header_fingerprint", sa.String(length=64)))
    op.add_column("import", sa.Column("mapping", postgresql.JSONB()))
    op.add_column("import", sa.Column("staged_payload", sa.LargeBinary()))
    op.add_column("import", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("import", sa.Column("reverted_at", sa.DateTime(timezone=True)))
    for column in (
        "transfers_imported",
        "categories_created",
        "subcategories_created",
        "duplicates_skipped",
        "rows_failed",
    ):
        op.add_column("import", sa.Column(column, sa.Integer()))

    for table in ("category", "subcategory", "transfer"):
        op.add_column(
            table,
            sa.Column(
                "import_id", sa.Integer(), sa.ForeignKey("import.id"), nullable=True
            ),
        )
    for table in ("category", "subcategory", "transfer", "expense", "income", "account", "place"):
        op.create_index(f"ix_{table}_import_id", table, ["import_id"])

    op.create_table(
        "import_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("header_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("headers", postgresql.JSONB(), nullable=False),
        sa.Column("column_mapping", postgresql.JSONB(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False),
        sa.Column(
            "value_mappings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "owner_id", "header_fingerprint", name="uq_import_profile_owner_headers"
        ),
    )
    op.create_index("ix_import_profile_id", "import_profile", ["id"])
    op.create_index("ix_import_profile_owner_id", "import_profile", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_import_profile_owner_id", table_name="import_profile")
    op.drop_index("ix_import_profile_id", table_name="import_profile")
    op.drop_table("import_profile")

    for table in ("category", "subcategory", "transfer", "expense", "income", "account", "place"):
        op.drop_index(f"ix_{table}_import_id", table_name=table)
    for table in ("category", "subcategory", "transfer"):
        op.drop_column(table, "import_id")

    for column in (
        "rows_failed",
        "duplicates_skipped",
        "subcategories_created",
        "categories_created",
        "transfers_imported",
        "reverted_at",
        "expires_at",
        "staged_payload",
        "mapping",
        "header_fingerprint",
    ):
        op.drop_column("import", column)
    op.drop_index("ix_import_file_hash", table_name="import")
    op.drop_column("import", "file_hash")
    op.drop_column("import", "filename")
    op.execute("UPDATE import SET file_content = '' WHERE file_content IS NULL")
    op.alter_column("import", "file_content", existing_type=sa.String(), nullable=False)
    op.drop_index("ix_import_owner_id", table_name="import")
    op.drop_index("ix_import_status", table_name="import")
    op.drop_column("import", "status")
    IMPORT_STATUS.drop(op.get_bind(), checkfirst=True)
