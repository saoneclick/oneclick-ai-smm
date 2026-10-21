import json
from typing import Any

import asyncpg

from config import settings

_pool: asyncpg.Pool | None = None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS smm_sessions (
    id          bigserial PRIMARY KEY,
    chat_id     bigint NOT NULL,
    status      text NOT NULL DEFAULT 'active',
    step        text NOT NULL DEFAULT 'goal',
    data        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS smm_sessions_one_active
    ON smm_sessions (chat_id) WHERE status IN ('active', 'generating', 'preview');

ALTER TABLE content_drafts ADD COLUMN IF NOT EXISTS brief jsonb;
ALTER TABLE content_drafts ADD COLUMN IF NOT EXISTS strategy jsonb;
ALTER TABLE content_drafts ADD COLUMN IF NOT EXISTS images jsonb;
ALTER TABLE content_drafts ADD COLUMN IF NOT EXISTS quality_score numeric;
ALTER TABLE content_drafts ADD COLUMN IF NOT EXISTS goal text;
ALTER TABLE content_drafts ADD COLUMN IF NOT EXISTS audience text;
ALTER TABLE content_drafts ADD COLUMN IF NOT EXISTS dzen_url text;
"""


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda value: json.dumps(value, ensure_ascii=False),
        decoder=json.loads,
        schema="pg_catalog",
    )


async def connect() -> None:
    global _pool
    if _pool is not None:
        return
    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=1,
        max_size=6,
        init=_init_connection,
    )
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool is not initialized")
    return _pool


async def get_active_session(chat_id: int) -> dict[str, Any] | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, chat_id, status, step, data, created_at, updated_at
            FROM smm_sessions
            WHERE chat_id=$1 AND status IN ('active', 'generating', 'preview')
            ORDER BY id DESC LIMIT 1
            """,
            chat_id,
        )
        return dict(row) if row else None


async def start_session(chat_id: int, step: str, data: dict[str, Any], status: str = "active") -> dict[str, Any]:
    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE smm_sessions SET status='cancelled', updated_at=now() "
                "WHERE chat_id=$1 AND status IN ('active', 'generating', 'preview')",
                chat_id,
            )
            row = await conn.fetchrow(
                "INSERT INTO smm_sessions (chat_id, step, data, status) "
                "VALUES ($1, $2, $3, $4) RETURNING id, chat_id, status, step, data",
                chat_id,
                step,
                data,
                status,
            )
            return dict(row)


async def update_session(
    session_id: int,
    step: str | None = None,
    data: dict[str, Any] | None = None,
    status: str | None = None,
) -> None:
    sets = ["updated_at=now()"]
    args: list[Any] = []
    if step is not None:
        args.append(step)
        sets.append(f"step=${len(args)}")
    if data is not None:
        args.append(data)
        sets.append(f"data=${len(args)}")
    if status is not None:
        args.append(status)
        sets.append(f"status=${len(args)}")
    args.append(session_id)
    async with pool().acquire() as conn:
        await conn.execute(
            f"UPDATE smm_sessions SET {', '.join(sets)} WHERE id=${len(args)}",
            *args,
        )


async def close_session(session_id: int, status: str = "cancelled") -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE smm_sessions SET status=$2, data=data || $3::jsonb, updated_at=now() WHERE id=$1",
            session_id,
            status,
            {"closed_at": "now"},
        )


async def get_style_rules() -> list[str]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT rule FROM style_rules WHERE active=true ORDER BY id ASC"
        )
        return [r["rule"] for r in rows]


async def get_recent_posts(limit: int = 3) -> list[str]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tg_text FROM content_drafts
            WHERE status='published' AND tg_text IS NOT NULL AND length(tg_text) > 80
            ORDER BY id DESC LIMIT $1
            """,
            limit,
        )
        return [r["tg_text"] for r in rows]


async def insert_draft(
    *,
    topic: str,
    tg_text: str,
    vk_text: str,
    max_text: str,
    headline: str,
    body: str,
    image_url: str,
    channels: list[str],
    brief: dict[str, Any],
    strategy: dict[str, Any],
    images: list[dict[str, Any]],
    quality_score: float | None = None,
    goal: str = "",
    audience: str = "",
    status: str = "draft",
) -> int:
    async with pool().acquire() as conn:
        return int(
            await conn.fetchval(
                """
                INSERT INTO content_drafts
                    (topic, tg_text, vk_text, max_text, source, headline, body, image_url,
                     channels, status, brief, strategy, images, quality_score, goal, audience)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                RETURNING id
                """,
                topic,
                tg_text,
                vk_text,
                max_text,
                "smm",
                headline,
                body,
                image_url,
                ",".join(channels),
                status,
                brief,
                strategy,
                images,
                quality_score,
                goal,
                audience,
            )
        )


async def update_draft_content(
    draft_id: int,
    *,
    tg_text: str,
    vk_text: str,
    max_text: str,
    headline: str,
    body: str,
    image_url: str,
    strategy: dict[str, Any],
    images: list[dict[str, Any]],
    quality_score: float | None,
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE content_drafts SET
                tg_text=$2, vk_text=$3, max_text=$4, headline=$5, body=$6,
                image_url=$7, strategy=$8, images=$9, quality_score=$10, updated_at=now()
            WHERE id=$1
            """,
            draft_id,
            tg_text,
            vk_text,
            max_text,
            headline,
            body,
            image_url,
            strategy,
            images,
            quality_score,
        )


async def get_draft(draft_id: int) -> dict[str, Any] | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, topic, tg_text, vk_text, max_text, headline, body, image_url,
                   channels, status, images, brief, strategy, goal, audience,
                   quality_score, scheduled_at, published_tg, published_vk, published_max
            FROM content_drafts WHERE id=$1
            """,
            draft_id,
        )
        return dict(row) if row else None


async def delete_draft(draft_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM content_drafts WHERE id=$1", draft_id)


async def mark_scheduled(draft_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE content_drafts SET status='smm_scheduled',
                scheduled_at=(
                    date_trunc('day', now() AT TIME ZONE 'Asia/Yekaterinburg')
                    + interval '1 day 9 hours'
                ) AT TIME ZONE 'Asia/Yekaterinburg',
                updated_at=now()
            WHERE id=$1
            """,
            draft_id,
        )


async def due_scheduled() -> list[dict[str, Any]]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM content_drafts
            WHERE status='smm_scheduled' AND scheduled_at IS NOT NULL AND scheduled_at <= now()
            ORDER BY scheduled_at ASC LIMIT 5
            """
        )
        return [dict(r) for r in rows]


async def mark_published(
    draft_id: int,
    *,
    tg_message_id: int | None = None,
    vk_post_id: int | None = None,
    max_message_id: str | None = None,
    dzen_url: str | None = None,
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE content_drafts SET
                status='published', published_tg=COALESCE($2, published_tg),
                published_vk=COALESCE($3, published_vk),
                published_max=COALESCE($4, published_max),
                dzen_url=COALESCE($5, dzen_url),
                updated_at=now()
            WHERE id=$1
            """,
            draft_id,
            tg_message_id,
            vk_post_id,
            max_message_id,
            dzen_url,
        )


async def mark_status(draft_id: int, status: str) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE content_drafts SET status=$2, updated_at=now() WHERE id=$1",
            draft_id,
            status,
        )


async def log_feedback(
    draft_id: int | None,
    action: str,
    reason_code: str | None = None,
    reason_text: str | None = None,
    angle: str | None = None,
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO feedback (draft_id, action, reason_code, reason_text, angle) "
            "VALUES ($1,$2,$3,$4,$5)",
            draft_id,
            action,
            reason_code,
            reason_text,
            angle,
        )
