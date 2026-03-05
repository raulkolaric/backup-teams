import asyncio
from api.dependencies.db import get_pool
from api.services.s3_stats import get_bucket_stats
from dotenv import load_dotenv
import asyncpg
import os

load_dotenv()

async def main():
    dsn = f"postgresql://{os.environ.get('DB_USER')}:{os.environ.get('DB_PASSWORD')}@{os.environ.get('DB_HOST')}:{os.environ.get('DB_PORT')}/{os.environ.get('DB_NAME')}"
    pool = await asyncpg.create_pool(dsn)
    
    print("Testing DB queries...")
    totals = await pool.fetchrow(
        """
        SELECT
            COUNT(*)                                            AS total_files,
            COUNT(*) FILTER (WHERE file_extension = 'pdf'
                             AND content_text IS NOT NULL)      AS indexed_pdfs,
            (SELECT COUNT(*) FROM class)                        AS total_classes,
            (SELECT COUNT(*) FROM curso)                        AS total_cursos
        FROM archive
        """
    )
    print("Totals:", totals)

    by_ext = await pool.fetch(
        """
        SELECT file_extension AS extension, COUNT(*) AS cnt
        FROM archive
        WHERE file_extension IS NOT NULL
        GROUP BY file_extension
        ORDER BY cnt DESC
        """
    )
    print("By ext:", by_ext)
    
    print("Testing S3 stats...")
    s3_stats = await get_bucket_stats()
    print("S3 Stats:", s3_stats)
    
    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
