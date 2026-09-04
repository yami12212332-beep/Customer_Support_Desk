import os
import json
import asyncio
import asyncpg

from dotenv import load_dotenv
load_dotenv(r"C:\Users\Sachin\Documents\Use Cases\Customer_Support_Desk\.env")

_pool: asyncpg.Pool | None = None

async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )

async def init_pool(dsn: str | None = None, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool

    dsn = dsn or os.environ["DATABASE_URL"]
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size, init=_init_connection)
    return _pool

async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError(
            "DB pool not initialized. Call init_pool() at app startup "
            "(e.g. in FastAPI's lifespan handler) before using any tool functions."
        )
    return _pool

async def test_connection():
    pool = await init_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM accounts WHERE account_id = $1",
            1,
        )
        print(f"accounts row: {dict(row) if row else 'no row found for account_id=1'}")

async def main():
    try:
        await test_connection()
    except Exception as e:
        print("Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
