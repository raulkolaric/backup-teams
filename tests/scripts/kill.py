import os
import asyncio
import asyncpg
from dotenv import load_dotenv

async def main():
    load_dotenv()
    dsn = f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}?sslmode=require"
    try:
        conn = await asyncpg.connect(dsn)
        # Terminate all connections except this one
        await conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid();")
        await conn.close()
        print("Terminated other active connections.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
