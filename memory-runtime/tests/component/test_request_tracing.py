import json
import logging
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import Base, get_engine, get_session_factory, reset_database_caches
from app.main import create_app
from app.models.job import Job
from app.workers.runner import WorkerRunner


class RequestTracingTests(unittest.TestCase):
    """One request ID ties the bridge call, the API request and the worker's outcome.

    The 0.1.61 AMD screening could not tell a timeout from a runtime error,
    because no hop logged the call. Logs must still never contain memory text.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["MEMORY_RUNTIME_POSTGRES_DSN"] = f"sqlite+pysqlite:///{os.path.join(self.temp_dir.name, 't.db')}"
        os.environ["MEMORY_RUNTIME_AUTO_CREATE_TABLES"] = "true"
        os.environ["MEMORY_RUNTIME_ENV"] = "test"
        get_settings.cache_clear()
        reset_database_caches()
        Base.metadata.create_all(bind=get_engine())
        self.client = TestClient(create_app())
        self.namespace_id = self.client.post(
            "/v1/namespaces", json={"name": "bunker:user:u1", "mode": "isolated", "source_systems": ["bunkerai"]}
        ).json()["id"]
        self.agent_id = self.client.post(
            f"/v1/namespaces/{self.namespace_id}/agents", json={"name": "bunker-core", "source_system": "bunkerai"}
        ).json()["id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key in ("MEMORY_RUNTIME_POSTGRES_DSN", "MEMORY_RUNTIME_AUTO_CREATE_TABLES", "MEMORY_RUNTIME_ENV"):
            os.environ.pop(key, None)
        get_settings.cache_clear()
        reset_database_caches()

    def test_request_id_reaches_the_api_log_the_job_and_the_worker_outcome(self) -> None:
        secret = "Секретный код склада ZX-4471"
        with self.assertLogs(level=logging.INFO) as captured:
            response = self.client.post(
                "/v1/events",
                headers={"X-Request-ID": "mem_trace_7"},
                json={
                    "namespace_id": self.namespace_id,
                    "agent_id": self.agent_id,
                    "session_id": "general",
                    "source_system": "bunkerai",
                    "event_type": "conversation_turn",
                    "messages": [
                        {"role": "user", "content": f"Запомни: {secret}"},
                        {"role": "assistant", "content": "Принято"},
                    ],
                    "dedupe_key": "usr_private:msg_1",
                },
            )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.headers["x-request-id"], "mem_trace_7")
            session = get_session_factory()()
            try:
                jobs = session.query(Job).all()
                self.assertTrue(jobs)
                self.assertTrue(all(job.payload_json.get("request_id") == "mem_trace_7" for job in jobs))
            finally:
                session.close()
            WorkerRunner.run_pending_jobs()

        records = []
        for line in captured.output:
            try:
                records.append(json.loads(line.split(":", 2)[2]))
            except (IndexError, ValueError):
                continue
        events = {record["event"]: record for record in records if record.get("request_id") == "mem_trace_7"}
        self.assertEqual(events["http.request"]["route"], "/v1/events")
        self.assertEqual(events["http.request"]["status"], 201)
        self.assertIn("ingestion.persisted", events)
        self.assertIn("worker.job.completed", events)
        self.assertTrue(any(name.startswith("consolidation.") for name in events), sorted(events))
        text = "\n".join(captured.output)
        for leaked in ("ZX-4471", "Секретный", "usr_private"):
            self.assertNotIn(leaked, text)

    def test_malformed_request_id_is_replaced_and_failures_are_logged_with_route(self) -> None:
        with self.assertLogs("app.http", level=logging.INFO) as captured:
            response = self.client.get(
                "/v1/adapters/bunkerai/memories/unknown-id",
                params={"namespace_id": self.namespace_id},
                headers={"X-Request-ID": "bad id with spaces"},
            )
        self.assertEqual(response.status_code, 404)
        self.assertRegex(response.headers["x-request-id"], r"^rt_[0-9a-f]{16}$")
        record = json.loads(captured.output[-1].split(":", 2)[2])
        self.assertEqual(record["route"], "/v1/adapters/bunkerai/memories/{memory_id}")
        self.assertEqual(record["status"], 404)
        self.assertNotIn("unknown-id", captured.output[-1])
