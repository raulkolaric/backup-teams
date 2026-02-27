import os
import asyncio
import asyncpg
from dotenv import load_dotenv

async def main():
    load_dotenv()
    dsn = f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}?sslmode=require"
    try:
        conn = await asyncpg.connect(dsn, timeout=5)
        # Check active connections
        rows = await conn.fetch("SELECT pid, state, query FROM pg_stat_activity WHERE datname = $1", os.environ['DB_NAME'])
        print(f"Num connections: {len(rows)}")
        for r in rows:
            print(dict(r))
        await conn.close()
    except Exception as e:
        print(f"Error checking connections: {e}")

if __name__ == "__main__":
    asyncio.run(main())
