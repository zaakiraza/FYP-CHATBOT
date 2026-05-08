from __future__ import annotations

import asyncio

from motor.motor_asyncio import AsyncIOMotorClient

from app.config import get_settings


async def main() -> None:
    s = get_settings()
    c = AsyncIOMotorClient(s.mongodb_uri)
    db = c[s.mongodb_db_name]

    names = await db.list_collection_names()
    for n in sorted(names):
        try:
            cnt = await db[n].count_documents({})
        except Exception as e:  # pragma: no cover
            print(f"{n}: ERROR {e}")
            continue
        if cnt:
            print(f"{n}: {cnt}")

    c.close()


if __name__ == "__main__":
    asyncio.run(main())

