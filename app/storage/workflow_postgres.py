"""Durable tasks with a session lock and checkpointer on the SAME connection.

Losing the lock connection also prevents the old executor from committing
checkpoints; a replacement worker may then resume the last committed node.
"""

from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Error
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.storage.workflow_base import RunBusy, RunConflict, RunLease, RunNotFound


SCHEMA = """
CREATE TABLE IF NOT EXISTS tailoring_runs (
    thread_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    target TEXT NOT NULL,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tailoring_runs_pending
    ON tailoring_runs(updated_at) WHERE status IN ('queued', 'running', 'cancelling', 'saving');
"""


class PostgresRunStore:
    def __init__(self, database_url, *, pool_size=8):
        self.pool = ConnectionPool(
            database_url, min_size=1, max_size=pool_size, open=False,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
            timeout=10,
        )
        self.pool.open(wait=True, timeout=10)
        try:
            with self.pool.connection() as conn:
                # Setup includes concurrent-index migrations, so use autocommit
                # plus a session advisory lock rather than a transaction.
                conn.execute("SELECT pg_advisory_lock(hashtextextended('tailoring_schema_v2', 0))")
                try:
                    conn.execute(SCHEMA, prepare=False)
                    PostgresSaver(conn).setup()
                finally:
                    conn.execute("SELECT pg_advisory_unlock(hashtextextended('tailoring_schema_v2', 0))")
        except Exception:
            self.pool.close()
            raise

    @staticmethod
    def _record(row):
        if row is None:
            raise RunNotFound("生成任务不存在。")
        return {
            **row["payload"],
            **{key: row[key] for key in ("thread_id", "status", "target", "cancel_requested")},
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    def create(self, thread_id, payload, target):
        with self.pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO tailoring_runs(thread_id,status,target,payload) VALUES (%s,'queued',%s,%s)
                   ON CONFLICT(thread_id) DO NOTHING RETURNING *""",
                (thread_id, target, Jsonb(payload)),
            ).fetchone()
            existing = self._record(row) if row else self._get(conn, thread_id)
            if existing["input_hash"] != payload["input_hash"]:
                raise RunConflict("同一请求 ID 不能用于不同输入。")
            return existing

    def _get(self, conn, thread_id):
        return self._record(conn.execute(
            "SELECT * FROM tailoring_runs WHERE thread_id = %s", (thread_id,),
        ).fetchone())

    def get(self, thread_id):
        with self.pool.connection() as conn:
            return self._get(conn, thread_id)

    def _update(self, conn, thread_id, **changes):
        status = changes.pop("status", None)
        return self._record(conn.execute(
            """UPDATE tailoring_runs SET payload=payload || %s,
               status=CASE WHEN cancel_requested AND %s IN ('queued','failed','initial_ready')
                   THEN 'cancelled' ELSE COALESCE(%s,status) END,
               updated_at=CURRENT_TIMESTAMP
               WHERE thread_id=%s RETURNING *""",
            (Jsonb(changes), status, status, thread_id),
        ).fetchone())

    def pending(self, limit=32):
        with self.pool.connection() as conn:
            rows = conn.execute(
                """SELECT thread_id FROM tailoring_runs
                   WHERE status IN ('queued','running','cancelling','saving')
                   ORDER BY updated_at LIMIT %s""", (limit,),
            ).fetchall()
        return [row["thread_id"] for row in rows]

    def resume(self, thread_id):
        with self.pool.connection() as conn:
            row = conn.execute(
                """UPDATE tailoring_runs SET status='queued', target='full',
                   payload=payload || '{"error":null,"failed_node":null}'::jsonb,
                   updated_at=CURRENT_TIMESTAMP
                   WHERE thread_id=%s AND status IN ('failed','initial_ready')
                   AND NOT cancel_requested RETURNING *""", (thread_id,),
            ).fetchone()
            result = self._record(row) if row else self._get(conn, thread_id)
            if result["cancel_requested"]:
                raise RunConflict("任务已取消，请重新生成。")
            return result

    def cancel(self, thread_id):
        with self.pool.connection() as conn:
            row = conn.execute(
                """UPDATE tailoring_runs SET cancel_requested=TRUE,
                   status=CASE WHEN status IN ('running','cancelling') THEN 'cancelling' ELSE 'cancelled' END,
                   updated_at=CURRENT_TIMESTAMP
                   WHERE thread_id=%s AND status NOT IN ('saving','awaiting_confirmation','needs_attention','completed')
                   RETURNING *""", (thread_id,),
            ).fetchone()
            if row:
                return self._record(row)
            self._get(conn, thread_id)
            raise RunConflict("任务已进入结果保存阶段，无法取消。")

    def _begin_finalizing(self, conn, thread_id):
        return conn.execute(
            """UPDATE tailoring_runs SET status='saving', updated_at=CURRENT_TIMESTAMP
               WHERE thread_id=%s AND NOT cancel_requested RETURNING thread_id""", (thread_id,),
        ).fetchone() is not None

    @contextmanager
    def lease(self, thread_id, *, wait=False):
        with self.pool.connection() as conn:
            if wait:
                conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (thread_id,))
                acquired = True
            else:
                acquired = conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS acquired", (thread_id,),
                ).fetchone()["acquired"]
            if not acquired:
                raise RunBusy("该任务正在处理。")
            try:
                self._get(conn, thread_id)
                saver = PostgresSaver(conn)

                def locked(operation, *args, **kwargs):
                    # Saver pipelines can run in graph executor threads. Keep all
                    # task queries out of the same connection's pipeline region.
                    with saver.lock:
                        return operation(conn, thread_id, *args, **kwargs)

                yield RunLease(
                    saver,
                    lambda: locked(self._get),
                    lambda **changes: locked(self._update, **changes),
                    lambda: locked(self._begin_finalizing),
                )
            finally:
                # Do not let a reconnect release a different session's lock.
                if not conn.closed and not conn.broken:
                    try:
                        conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (thread_id,))
                    except Error:
                        conn.close()

    def close(self):
        self.pool.close()
