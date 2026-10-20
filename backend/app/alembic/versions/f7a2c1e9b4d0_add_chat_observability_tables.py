"""add chat observability tables

Revision ID: f7a2c1e9b4d0
Revises: c4f8a2b91e03
Create Date: 2026-06-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f7a2c1e9b4d0"
down_revision = "c4f8a2b91e03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chat_obs_conversations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_obs_conversations_user_id",
        "chat_obs_conversations",
        ["user_id"],
    )

    op.create_table(
        "chat_obs_turns",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False, server_default="openrouter"),
        sa.Column("user_message", sa.Text(), nullable=True),
        sa.Column("assistant_reply", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("rounds_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actions_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "pending_confirmation", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "pending_question", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("total_prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("total_completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_obs_turns_conversation_id", "chat_obs_turns", ["conversation_id"])
    op.create_index("ix_chat_obs_turns_user_id", "chat_obs_turns", ["user_id"])
    op.create_index("ix_chat_obs_turns_trace_id", "chat_obs_turns", ["trace_id"])
    op.create_index("ix_chat_obs_turns_request_id", "chat_obs_turns", ["request_id"])
    op.create_index("ix_chat_obs_turns_status", "chat_obs_turns", ["status"])
    op.create_index(
        "ix_chat_obs_turns_model_started_at",
        "chat_obs_turns",
        ["model", "started_at"],
    )

    op.create_table(
        "chat_obs_llm_calls",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("turn_id", sa.BigInteger(), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False, server_default="openrouter"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("finish_reason", sa.String(), nullable=True),
        sa.Column("had_tool_calls", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["turn_id"], ["chat_obs_turns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_obs_llm_calls_turn_id", "chat_obs_llm_calls", ["turn_id"])
    op.create_index(
        "ix_chat_obs_llm_calls_model_created_at",
        "chat_obs_llm_calls",
        ["model", "created_at"],
    )

    op.create_table(
        "chat_obs_tool_calls",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("turn_id", sa.BigInteger(), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="completed"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["turn_id"], ["chat_obs_turns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_obs_tool_calls_turn_id", "chat_obs_tool_calls", ["turn_id"])
    op.create_index("ix_chat_obs_tool_calls_tool_name", "chat_obs_tool_calls", ["tool_name"])
    op.create_index("ix_chat_obs_tool_calls_status", "chat_obs_tool_calls", ["status"])
    op.create_index(
        "ix_chat_obs_tool_calls_tool_name_status_created_at",
        "chat_obs_tool_calls",
        ["tool_name", "status", "created_at"],
    )

    op.create_table(
        "chat_model_pricing",
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False, server_default="openrouter"),
        sa.Column("input_price_per_1m", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("output_price_per_1m", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("cached_input_price_per_1m", sa.Numeric(10, 6), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("model"),
    )

    # Migrate data from the deprecated chatturnlog table (created by the
    # recovered orphan revision c4f8a2b91e03) into the new normalized
    # chat_obs_* tables, keeping it as an archive. Guarded so fresh databases
    # skip it.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.chatturnlog') IS NOT NULL THEN
                -- conversations
                INSERT INTO chat_obs_conversations (id, user_id, title, message_count)
                SELECT conversation_id, owner_id, left(user_message, 60), 0
                FROM chatturnlog
                WHERE conversation_id IS NOT NULL
                GROUP BY conversation_id, owner_id, left(user_message, 60)
                ON CONFLICT (id) DO NOTHING;

                -- turns
                INSERT INTO chat_obs_turns (
                    conversation_id, user_id, request_id, model, provider,
                    user_message, assistant_reply, status, error, duration_ms,
                    rounds_count, actions_count, pending_confirmation, pending_question,
                    total_prompt_tokens, total_completion_tokens, total_tokens, total_cost_usd,
                    started_at, ended_at
                )
                SELECT
                    conversation_id, owner_id, request_id, model, 'openrouter',
                    user_message, assistant_reply,
                    CASE status
                        WHEN 'success' THEN 'ok'
                        ELSE status
                    END,
                    error_message, duration_ms,
                    llm_rounds, actions_count, pending_confirmation, pending_question,
                    prompt_tokens, completion_tokens, total_tokens, cost_usd,
                    created_at, created_at
                FROM chatturnlog
                ORDER BY id;

                -- llm calls from usage_detail jsonb array
                INSERT INTO chat_obs_llm_calls (
                    turn_id, round_index, model, provider,
                    prompt_tokens, completion_tokens, total_tokens, cached_tokens,
                    cost_usd, had_tool_calls
                )
                SELECT
                    t.id,
                    (ud.ord::int) - 1,
                    COALESCE(c.model_used, c.model),
                    'openrouter',
                    NULLIF(ud.elem->>'prompt_tokens', '')::int,
                    NULLIF(ud.elem->>'completion_tokens', '')::int,
                    NULLIF(ud.elem->>'total_tokens', '')::int,
                    COALESCE(NULLIF((ud.elem->'prompt_tokens_details')->>'cached_tokens', '')::int, 0),
                    NULLIF(ud.elem->>'cost', '')::numeric,
                    FALSE
                FROM chatturnlog c
                JOIN chat_obs_turns t
                  ON t.conversation_id = c.conversation_id
                 AND t.started_at = c.created_at
                 AND COALESCE(t.user_message, '') = COALESCE(c.user_message, '')
                CROSS JOIN LATERAL jsonb_array_elements(
                    c.usage_detail
                ) WITH ORDINALITY AS ud(elem, ord)
                WHERE c.usage_detail IS NOT NULL
                  AND jsonb_typeof(c.usage_detail) = 'array';

                -- tool calls from tool_steps jsonb array
                INSERT INTO chat_obs_tool_calls (
                    turn_id, round_index, tool_name, status, duration_ms
                )
                SELECT
                    t.id,
                    0,
                    ts.elem->>'tool_name',
                    CASE ts.elem->>'status'
                        WHEN 'completed' THEN 'completed'
                        WHEN 'failed' THEN 'failed'
                        WHEN 'pending_confirmation' THEN 'pending_confirmation'
                        ELSE 'completed'
                    END,
                    COALESCE(NULLIF(ts.elem->>'duration_ms', '')::int, 0)
                FROM chatturnlog c
                JOIN chat_obs_turns t
                  ON t.conversation_id = c.conversation_id
                 AND t.started_at = c.created_at
                 AND COALESCE(t.user_message, '') = COALESCE(c.user_message, '')
                CROSS JOIN LATERAL jsonb_array_elements(
                    c.tool_steps
                ) WITH ORDINALITY AS ts(elem, ord)
                WHERE c.tool_steps IS NOT NULL
                  AND jsonb_typeof(c.tool_steps) = 'array';

                -- chatturnlog is kept: the copy above is lossy, so the
                -- original rows stay as an archive. Drop it manually once
                -- the chat_obs_* data has been checked.
            END IF;
        END $$;
        """
    )


def downgrade():
    op.drop_table("chat_model_pricing")
    op.drop_index("ix_chat_obs_tool_calls_tool_name_status_created_at", table_name="chat_obs_tool_calls")
    op.drop_index("ix_chat_obs_tool_calls_status", table_name="chat_obs_tool_calls")
    op.drop_index("ix_chat_obs_tool_calls_tool_name", table_name="chat_obs_tool_calls")
    op.drop_index("ix_chat_obs_tool_calls_turn_id", table_name="chat_obs_tool_calls")
    op.drop_table("chat_obs_tool_calls")
    op.drop_index("ix_chat_obs_llm_calls_model_created_at", table_name="chat_obs_llm_calls")
    op.drop_index("ix_chat_obs_llm_calls_turn_id", table_name="chat_obs_llm_calls")
    op.drop_table("chat_obs_llm_calls")
    op.drop_index("ix_chat_obs_turns_model_started_at", table_name="chat_obs_turns")
    op.drop_index("ix_chat_obs_turns_status", table_name="chat_obs_turns")
    op.drop_index("ix_chat_obs_turns_request_id", table_name="chat_obs_turns")
    op.drop_index("ix_chat_obs_turns_trace_id", table_name="chat_obs_turns")
    op.drop_index("ix_chat_obs_turns_user_id", table_name="chat_obs_turns")
    op.drop_index("ix_chat_obs_turns_conversation_id", table_name="chat_obs_turns")
    op.drop_table("chat_obs_turns")
    op.drop_index("ix_chat_obs_conversations_user_id", table_name="chat_obs_conversations")
    op.drop_table("chat_obs_conversations")
