from datetime import datetime, timedelta

import asyncpg

import config

pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    tg_id      BIGINT PRIMARY KEY,
    student_id TEXT UNIQUE NOT NULL,
    name       TEXT NOT NULL,
    elo        INT NOT NULL DEFAULT 1000 CHECK (elo >= 0),
    wins       INT NOT NULL DEFAULT 0,
    losses     INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS elo_history (
    id         BIGSERIAL PRIMARY KEY,
    tg_id      BIGINT NOT NULL REFERENCES players(tg_id) ON DELETE CASCADE,
    delta      INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_elo_history_time ON elo_history (created_at);
"""


async def init() -> None:
    global pool
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def close() -> None:
    if pool:
        await pool.close()


async def get_player(tg_id: int):
    return await pool.fetchrow("SELECT * FROM players WHERE tg_id = $1", tg_id)


async def create_player(tg_id: int, student_id: str, name: str) -> bool:
    """Returns False if this student id (or telegram id) is already registered."""
    try:
        await pool.execute(
            "INSERT INTO players (tg_id, student_id, name, elo) VALUES ($1, $2, $3, $4)",
            tg_id, student_id, name, config.START_ELO,
        )
        return True
    except asyncpg.UniqueViolationError:
        return False


async def apply_result(winner_id: int, loser_id: int, delta_win: int, delta_lose: int):
    """Applies a battle result. Elo never goes below 0.
    Returns (winner_elo, loser_elo, real_loser_delta)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            w = await conn.fetchrow(
                "SELECT elo FROM players WHERE tg_id = $1 FOR UPDATE", winner_id)
            l = await conn.fetchrow(
                "SELECT elo FROM players WHERE tg_id = $1 FOR UPDATE", loser_id)
            if w is None or l is None:  # somebody was deleted mid-battle
                return None
            new_w = w["elo"] + delta_win
            new_l = max(0, l["elo"] - delta_lose)
            real_lose = new_l - l["elo"]  # <= 0
            await conn.execute(
                "UPDATE players SET elo = $2, wins = wins + 1 WHERE tg_id = $1",
                winner_id, new_w)
            await conn.execute(
                "UPDATE players SET elo = $2, losses = losses + 1 WHERE tg_id = $1",
                loser_id, new_l)
            await conn.execute(
                "INSERT INTO elo_history (tg_id, delta) VALUES ($1, $2), ($3, $4)",
                winner_id, delta_win, loser_id, real_lose)
            return new_w, new_l, real_lose


async def top_alltime(limit: int = 100):
    return await pool.fetch(
        "SELECT tg_id, name, elo FROM players ORDER BY elo DESC, created_at LIMIT $1",
        limit)


async def top_period(since: datetime, limit: int = 100):
    """Players ranked by how much elo they gained since `since` (only positive)."""
    return await pool.fetch(
        """
        SELECT p.tg_id, p.name, SUM(h.delta)::int AS gain
        FROM elo_history h JOIN players p ON p.tg_id = h.tg_id
        WHERE h.created_at >= $1
        GROUP BY p.tg_id, p.name
        HAVING SUM(h.delta) > 0
        ORDER BY gain DESC LIMIT $2
        """,
        since, limit)


async def rank_of(elo: int) -> int:
    return 1 + await pool.fetchval("SELECT COUNT(*) FROM players WHERE elo > $1", elo)


def day_start(now: datetime) -> datetime:
    local = now.astimezone(config.LOCAL_TZ)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def week_start(now: datetime) -> datetime:
    return day_start(now) - timedelta(days=now.astimezone(config.LOCAL_TZ).weekday())


# ---- admin helpers ----
async def find_players(query: str):
    if query.lstrip("-").isdigit():
        return await pool.fetch(
            "SELECT * FROM players WHERE tg_id = $1 OR student_id = $2 LIMIT 10",
            int(query), query)
    return await pool.fetch(
        "SELECT * FROM players WHERE name ILIKE $1 OR student_id = $2 LIMIT 10",
        f"%{query}%", query)


async def delete_player(tg_id: int) -> bool:
    return (await pool.execute("DELETE FROM players WHERE tg_id = $1", tg_id)) == "DELETE 1"


async def set_elo(tg_id: int, elo: int) -> bool:
    # manual fix: intentionally NOT written to elo_history (does not affect daily/weekly tops)
    return (await pool.execute(
        "UPDATE players SET elo = $2 WHERE tg_id = $1", tg_id, max(0, elo))) == "UPDATE 1"


async def stats():
    return await pool.fetchrow(
        """
        SELECT COUNT(*) AS players,
               COALESCE(SUM(wins), 0) AS battles,
               COALESCE(MAX(elo), 0) AS max_elo
        FROM players
        """)


async def all_ids() -> list[int]:
    return [r["tg_id"] for r in await pool.fetch("SELECT tg_id FROM players")]
