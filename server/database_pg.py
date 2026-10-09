from dotenv import load_dotenv
import os   
import asyncpg
import asyncio
from fastapi import Request
from models import DetectionEventOut
load_dotenv()

async def init_schema(pool):
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                node_id TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                received_at TIMESTAMPTZ NOT NULL,
                location_km REAL NOT NULL,
                event_type TEXT NOT NULL,
                animal_type TEXT,
                confidence REAL,
                position TEXT,
                distance_from_rail_m REAL,
                animal_count INTEGER,
                severity_score REAL,
                severity_label TEXT,
                recommended_action TEXT,
                raw_json JSONB NOT NULL
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_node ON events(node_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")

async def get_db_connection(request: Request):
    pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        yield conn

async def init_pool():
    return await asyncpg.create_pool(
        host=os.environ.get("DB_HOST"),
        port=os.environ.get("DB_PORT"),
        database=os.environ.get("DB_NAME"),
        user=os.environ.get("DB_USER"),
        password=os.environ.get("DB_PASSWORD"),
        min_size=1,
        max_size=5,
    )

async def test_connection():
    _pool = await init_pool()
    async with _pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
        print("Connection test result:", result)
    await _pool.close()


async def save_event(pool, event: DetectionEventOut):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO events
            (event_id, node_id, timestamp, received_at, location_km, event_type,
             animal_type, confidence, position, distance_from_rail_m, animal_count,
             severity_score, severity_label, recommended_action, raw_json)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb)
            ON CONFLICT (event_id) DO UPDATE SET
                severity_score = EXCLUDED.severity_score,
                severity_label = EXCLUDED.severity_label
            """,
            event.event_id,
            event.node_id,
            event.timestamp,
            event.received_at,
            event.location_km,
            event.event_type.value,
            event.sensors.camera.animal_type,
            event.sensors.camera.confidence,
            event.position.value,
            event.distance_from_rail_m,
            event.animal_count,
            event.severity_score,
            event.severity_label.value,
            event.recommended_action,
            event.model_dump_json(),
        )

if __name__ == "__main__":
    asyncio.run(test_connection())