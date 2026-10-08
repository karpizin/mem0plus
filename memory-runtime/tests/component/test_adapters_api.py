import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import Base, get_engine, reset_database_caches
from app.main import create_app
from app.workers.runner import WorkerRunner


class AdaptersApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "adapters.db")
        os.environ["MEMORY_RUNTIME_POSTGRES_DSN"] = f"sqlite+pysqlite:///{self.db_path}"
        os.environ["MEMORY_RUNTIME_AUTO_CREATE_TABLES"] = "true"
        os.environ["MEMORY_RUNTIME_ENV"] = "test"
        get_settings.cache_clear()
        reset_database_caches()
        Base.metadata.create_all(bind=get_engine())
        self.client = TestClient(create_app())

        namespace_response = self.client.post(
            "/v1/namespaces",
            json={
                "name": "cluster:alpha:shared",
                "mode": "shared",
                "source_systems": ["openclaw", "bunkerai"],
            },
        )
        self.namespace_id = namespace_response.json()["id"]

        openclaw_agent = self.client.post(
            f"/v1/namespaces/{self.namespace_id}/agents",
            json={
                "name": "planner",
                "source_system": "openclaw",
            },
        )
        self.openclaw_agent_id = openclaw_agent.json()["id"]

        bunker_agent = self.client.post(
            f"/v1/namespaces/{self.namespace_id}/agents",
            json={
                "name": "researcher",
                "source_system": "bunkerai",
            },
        )
        self.bunkerai_agent_id = bunker_agent.json()["id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key in (
            "MEMORY_RUNTIME_POSTGRES_DSN",
            "MEMORY_RUNTIME_AUTO_CREATE_TABLES",
            "MEMORY_RUNTIME_ENV",
        ):
            os.environ.pop(key, None)
        get_settings.cache_clear()
        reset_database_caches()

    def test_openclaw_adapter_event_contract_wraps_ingestion_response(self) -> None:
        response = self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "session_id": "run_oc_1",
                "event_type": "conversation_turn",
                "messages": [
                    {"role": "user", "content": "Persist the cluster coordination note."},
                ],
                "metadata": {"project_id": "cluster-alpha"},
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["adapter"], "openclaw")
        self.assertEqual(payload["source_system"], "openclaw")
        self.assertEqual(payload["event"]["source_system"], "openclaw")
        self.assertEqual(payload["event"]["agent_id"], self.openclaw_agent_id)
        self.assertEqual(payload["event"]["project_id"], "cluster-alpha")
        self.assertIsNotNone(payload["event"]["episode_id"])

    def test_openclaw_bootstrap_creates_or_reuses_namespace_scope(self) -> None:
        response = self.client.post(
            "/v1/adapters/openclaw/bootstrap",
            json={
                "namespace_name": "openclaw:user:primary",
                "agent_name": "primary",
                "external_ref": "openclaw:user:primary",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["adapter"], "openclaw")
        self.assertEqual(payload["namespace_name"], "openclaw:user:primary")
        self.assertTrue(payload["namespace_id"])
        self.assertTrue(payload["agent_id"])

        second = self.client.post(
            "/v1/adapters/openclaw/bootstrap",
            json={
                "namespace_name": "openclaw:user:primary",
                "agent_name": "primary",
                "external_ref": "openclaw:user:primary",
            },
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["namespace_id"], payload["namespace_id"])
        self.assertEqual(second.json()["agent_id"], payload["agent_id"])

    def test_bunkerai_adapter_recall_contract_wraps_recall_response(self) -> None:
        self.client.post(
            "/v1/adapters/bunkerai/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.bunkerai_agent_id,
                "session_id": "run_ba_1",
                "event_type": "conversation_turn",
                "messages": [
                    {"role": "assistant", "content": "BunkerAI tracks the release checklist for the memory runtime."},
                ],
            },
        )

        response = self.client.post(
            "/v1/adapters/bunkerai/recall",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.bunkerai_agent_id,
                "session_id": "run_ba_1",
                "query": "What is BunkerAI tracking for the memory runtime?",
                "context_budget_tokens": 600,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["adapter"], "bunkerai")
        self.assertEqual(payload["source_system"], "bunkerai")
        self.assertIn("brief", payload)
        self.assertIn("trace", payload)
        self.assertIn("recent_session_carryover", payload["brief"])
        self.assertGreaterEqual(payload["trace"]["candidate_count"], 1)

    def test_shared_namespace_supports_cross_agent_shared_memory_without_private_leakage(self) -> None:
        self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "session_id": "run_oc_2",
                "event_type": "architecture_decision",
                "space_hint": "shared-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "The shared deployment stack for the memory runtime is Postgres, Redis, and pgvector.",
                    }
                ],
            },
        )
        self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "session_id": "run_oc_3",
                "event_type": "policy_update",
                "space_hint": "agent-core",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Only the OpenClaw planner should use the bulletless status format.",
                    }
                ],
            },
        )

        response = self.client.post(
            "/v1/adapters/bunkerai/recall",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.bunkerai_agent_id,
                "session_id": "run_ba_2",
                "query": "What shared deployment stack do we use for the memory runtime?",
                "context_budget_tokens": 800,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        flattened_brief = " ".join(
            item
            for section in payload["brief"].values()
            for item in section
        )

        self.assertIn("Postgres, Redis, and pgvector", flattened_brief)
        self.assertNotIn("bulletless status format", flattened_brief)
        self.assertIn("shared-space", payload["trace"]["selected_space_types"])


class SensitiveAdapterPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "adapters_sensitive.db")
        os.environ["MEMORY_RUNTIME_POSTGRES_DSN"] = f"sqlite+pysqlite:///{self.db_path}"
        os.environ["MEMORY_RUNTIME_AUTO_CREATE_TABLES"] = "true"
        os.environ["MEMORY_RUNTIME_ENV"] = "test"
        os.environ["MEMORY_RUNTIME_SENSITIVE_MEMORY_POLICY"] = "mark"
        os.environ["MEMORY_RUNTIME_MASK_SENSITIVE_MEMORY_OUTPUTS"] = "true"
        get_settings.cache_clear()
        reset_database_caches()
        Base.metadata.create_all(bind=get_engine())
        self.client = TestClient(create_app())

        namespace_response = self.client.post(
            "/v1/namespaces",
            json={
                "name": "cluster:sensitive:isolated",
                "mode": "isolated",
                "source_systems": ["openclaw"],
            },
        )
        self.namespace_id = namespace_response.json()["id"]
        agent_response = self.client.post(
            f"/v1/namespaces/{self.namespace_id}/agents",
            json={"name": "planner", "source_system": "openclaw"},
        )
        self.agent_id = agent_response.json()["id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key in (
            "MEMORY_RUNTIME_POSTGRES_DSN",
            "MEMORY_RUNTIME_AUTO_CREATE_TABLES",
            "MEMORY_RUNTIME_ENV",
            "MEMORY_RUNTIME_SENSITIVE_MEMORY_POLICY",
            "MEMORY_RUNTIME_MASK_SENSITIVE_MEMORY_OUTPUTS",
        ):
            os.environ.pop(key, None)
        get_settings.cache_clear()
        reset_database_caches()

    def test_adapter_masks_sensitive_memory_when_policy_is_mark(self) -> None:
        event = self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.agent_id,
                "session_id": "run_sensitive_adapter_1",
                "event_type": "conversation_turn",
                "space_hint": "project-space",
                "messages": [
                    {"role": "user", "content": "The API key is sk-live-12345-abcdef for the billing service."},
                ],
            },
        )
        self.assertEqual(event.status_code, 201)
        WorkerRunner.run_pending_jobs()

        listed = self.client.get(
            f"/v1/adapters/openclaw/memories?namespace_id={self.namespace_id}&agent_id={self.agent_id}&limit=10"
        )
        self.assertEqual(listed.status_code, 200)
        payload = listed.json()
        self.assertTrue(payload["results"])
        first = payload["results"][0]
        self.assertIn("[sensitive] hidden content", first["memory"])
        self.assertNotIn("sk-live-12345-abcdef", first["memory"])
        self.assertTrue(first["metadata"]["sensitive"])
        self.assertTrue(first["metadata"]["masked"])
        self.assertEqual(first["metadata"]["sensitivity_reason"], "privacy_sensitive_secret")


class AdaptersApiContractRegressionTests(AdaptersApiTests):
    def test_shared_namespace_recall_excludes_other_agents_private_project_context(self) -> None:
        self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "session_id": "run_oc_private_project",
                "event_type": "conversation_turn",
                "space_hint": "project-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "OpenClaw private rollout note: use planner-only canary labels for the deployment.",
                    }
                ],
            },
        )
        self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "session_id": "run_oc_shared_project",
                "event_type": "architecture_decision",
                "space_hint": "shared-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Shared deployment stack for the memory runtime uses Postgres and Redis.",
                    }
                ],
            },
        )
        self.client.post(
            "/v1/adapters/bunkerai/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.bunkerai_agent_id,
                "session_id": "run_ba_private_project",
                "event_type": "conversation_turn",
                "space_hint": "project-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "BunkerAI private task note: maintain the research checklist for the shared deployment.",
                    }
                ],
            },
        )

        response = self.client.post(
            "/v1/adapters/bunkerai/recall",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.bunkerai_agent_id,
                "session_id": "run_ba_recall_private",
                "query": "What deployment context should BunkerAI remember for this runtime?",
                "context_budget_tokens": 900,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        flattened_brief = " ".join(item for section in payload["brief"].values() for item in section)

        self.assertIn("Shared deployment stack for the memory runtime uses Postgres and Redis", flattened_brief)
        self.assertIn("BunkerAI private task note", flattened_brief)
        self.assertNotIn("planner-only canary labels", flattened_brief)

    def test_shared_namespace_search_and_list_keep_private_project_boundaries(self) -> None:
        self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "event_type": "conversation_turn",
                "space_hint": "project-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "OpenClaw private migration note: keep the planner-only rollout journal in the private workspace.",
                    }
                ],
            },
        )
        self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "event_type": "architecture_decision",
                "space_hint": "shared-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Shared runtime note: the memory runtime cluster uses Postgres, Redis, and pgvector.",
                    }
                ],
            },
        )
        self.client.post(
            "/v1/adapters/bunkerai/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.bunkerai_agent_id,
                "event_type": "conversation_turn",
                "space_hint": "project-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "BunkerAI private project note: keep the research timeline aligned with the shared runtime milestone.",
                    }
                ],
            },
        )

        processed = WorkerRunner.run_pending_jobs()
        self.assertGreaterEqual(processed, 3)

        listed = self.client.get(
            f"/v1/adapters/openclaw/memories?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        self.assertEqual(listed.status_code, 200)
        listed_memories = [item["memory"] for item in listed.json()["results"]]

        self.assertTrue(any("Shared runtime note" in item for item in listed_memories))
        self.assertTrue(any("planner-only rollout journal" in item for item in listed_memories))
        self.assertFalse(any("BunkerAI private project note" in item for item in listed_memories))

        search = self.client.post(
            "/v1/adapters/openclaw/search",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "query": "What runtime and project notes should OpenClaw keep in mind?",
                "limit": 10,
            },
        )
        self.assertEqual(search.status_code, 200)
        search_memories = [item["memory"] for item in search.json()["results"]]

        self.assertTrue(any("Shared runtime note" in item for item in search_memories))
        self.assertTrue(any("planner-only rollout journal" in item for item in search_memories))
        self.assertFalse(any("BunkerAI private project note" in item for item in search_memories))

    def test_openclaw_search_list_get_and_delete_memory_contract(self) -> None:
        event = self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "session_id": "run_oc_4",
                "event_type": "conversation_turn",
                "space_hint": "project-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "The runtime provider should support search, list, get, and delete flows.",
                    }
                ],
            },
        )
        self.assertEqual(event.status_code, 201)
        episode_id = event.json()["event"]["episode_id"]
        processed = WorkerRunner.run_pending_jobs()
        self.assertGreaterEqual(processed, 1)

        search = self.client.post(
            "/v1/adapters/openclaw/search",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "query": "Which provider flows should the runtime support?",
                "limit": 5,
            },
        )
        self.assertEqual(search.status_code, 200)
        search_payload = search.json()
        self.assertTrue(search_payload["results"])
        self.assertEqual(search_payload["results"][0]["resource_kind"], "memory_unit")
        self.assertTrue(any("search, list, get, and delete" in item["memory"] for item in search_payload["results"]))

        listed = self.client.get(
            f"/v1/adapters/openclaw/memories?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        self.assertEqual(listed.status_code, 200)
        self.assertFalse(any(item["id"] == episode_id for item in listed.json()["results"]))
        self.assertTrue(
            any(
                item["resource_kind"] == "memory_unit"
                and "search, list, get, and delete" in item["memory"]
                for item in listed.json()["results"]
            )
        )

        fetched = self.client.get(
            f"/v1/adapters/openclaw/memories/{episode_id}?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertIn("search, list, get, and delete", fetched.json()["memory"])

        deleted = self.client.delete(
            f"/v1/adapters/openclaw/memories/{episode_id}?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        self.assertEqual(deleted.status_code, 204)

        missing = self.client.get(
            f"/v1/adapters/openclaw/memories/{episode_id}?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        self.assertEqual(missing.status_code, 404)

    def test_openclaw_session_scoped_search_and_list_return_episode_candidates(self) -> None:
        self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "session_id": "run_oc_session_contract",
                "event_type": "conversation_turn",
                "space_hint": "session-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Session-only scratchpad: compare retry timings before choosing the worker backoff window.",
                    }
                ],
            },
        )

        search = self.client.post(
            "/v1/adapters/openclaw/search",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "session_id": "run_oc_session_contract",
                "query": "What scratchpad note exists for the worker backoff window?",
                "limit": 5,
            },
        )
        self.assertEqual(search.status_code, 200)
        search_results = search.json()["results"]
        self.assertTrue(search_results)
        self.assertEqual(search_results[0]["resource_kind"], "episode")

        listed = self.client.get(
            f"/v1/adapters/openclaw/memories?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}&session_id=run_oc_session_contract"
        )
        self.assertEqual(listed.status_code, 200)
        listed_results = listed.json()["results"]
        self.assertTrue(listed_results)
        self.assertTrue(all(item["resource_kind"] == "episode" for item in listed_results))
        self.assertTrue(any("worker backoff window" in item["memory"] for item in listed_results))

    def test_openclaw_review_memory_updates_and_marks_incorrect_for_durable_memory(self) -> None:
        event = self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "event_type": "architecture_decision",
                "space_hint": "project-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "The runtime provider supports bootstrap and recall flows.",
                    }
                ],
            },
        )
        self.assertEqual(event.status_code, 201)
        processed = WorkerRunner.run_pending_jobs()
        self.assertGreaterEqual(processed, 1)

        listed = self.client.get(
            f"/v1/adapters/openclaw/memories?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        memory_id = next(
            item["id"]
            for item in listed.json()["results"]
            if item["resource_kind"] == "memory_unit" and "bootstrap and recall" in item["memory"]
        )

        updated = self.client.patch(
            f"/v1/adapters/openclaw/memories/{memory_id}?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}",
            json={
                "content": "The runtime provider supports bootstrap, recall, and review flows.",
                "reason": "Clarify that review is part of the contract.",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertIn("bootstrap, recall, and review", updated.json()["memory"])
        self.assertEqual(updated.json()["metadata"]["status"], "active")

        fetched = self.client.get(
            f"/v1/adapters/openclaw/memories/{memory_id}?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertIn("bootstrap, recall, and review", fetched.json()["memory"])

        search = self.client.post(
            "/v1/adapters/openclaw/search",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "query": "Which provider flows include review?",
                "limit": 5,
            },
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(any("bootstrap, recall, and review" in item["memory"] for item in search.json()["results"]))

        marked = self.client.patch(
            f"/v1/adapters/openclaw/memories/{memory_id}?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}",
            json={
                "mark_incorrect": True,
                "reason": "This contract note is outdated.",
            },
        )
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(marked.json()["metadata"]["status"], "incorrect")

        missing = self.client.get(
            f"/v1/adapters/openclaw/memories/{memory_id}?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        self.assertEqual(missing.status_code, 404)

        relisted = self.client.get(
            f"/v1/adapters/openclaw/memories?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        self.assertFalse(any(item["id"] == memory_id for item in relisted.json()["results"]))

    def test_openclaw_review_memory_updates_and_marks_incorrect_for_session_episode(self) -> None:
        event = self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "session_id": "run_oc_review_session",
                "event_type": "conversation_turn",
                "space_hint": "session-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Session scratchpad: compare Redis retry windows before rollout.",
                    }
                ],
            },
        )
        self.assertEqual(event.status_code, 201)
        episode_id = event.json()["event"]["episode_id"]

        updated = self.client.patch(
            f"/v1/adapters/openclaw/memories/{episode_id}?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}",
            json={
                "content": "Session scratchpad: compare Redis and Postgres retry windows before rollout.",
                "reason": "Keep the scratchpad aligned with the latest experiment.",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["resource_kind"], "episode")
        self.assertIn("Redis and Postgres retry windows", updated.json()["memory"])

        fetched = self.client.get(
            f"/v1/adapters/openclaw/memories/{episode_id}?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertIn("Redis and Postgres retry windows", fetched.json()["memory"])

        marked = self.client.patch(
            f"/v1/adapters/openclaw/memories/{episode_id}?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}",
            json={"mark_incorrect": True, "reason": "Scratchpad note should be dropped."},
        )
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(marked.json()["metadata"]["status"], "incorrect")

        missing = self.client.get(
            f"/v1/adapters/openclaw/memories/{episode_id}?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        self.assertEqual(missing.status_code, 404)

    def test_long_term_search_and_list_exclude_session_memory_and_deduplicate_consolidated_episodes(self) -> None:
        self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "event_type": "architecture_decision",
                "space_hint": "project-space",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "The pilot runtime stack is Postgres, Redis, and a dedicated memory worker.",
                    }
                ],
            },
        )
        self.client.post(
            "/v1/adapters/openclaw/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "session_id": "run_oc_session_scope",
                "event_type": "conversation_turn",
                "space_hint": "session-space",
                "messages": [
                    {
                        "role": "user",
                        "content": "Remember to prepare the runbook acceptance checklist for this session.",
                    }
                ],
            },
        )

        processed = WorkerRunner.run_pending_jobs()
        self.assertGreaterEqual(processed, 2)

        listed = self.client.get(
            f"/v1/adapters/openclaw/memories?namespace_id={self.namespace_id}&agent_id={self.openclaw_agent_id}"
        )
        self.assertEqual(listed.status_code, 200)
        listed_memories = [item["memory"] for item in listed.json()["results"]]
        durable_matches = [item for item in listed_memories if "dedicated memory worker" in item]
        self.assertEqual(len(durable_matches), 1)
        self.assertFalse(any("runbook acceptance checklist" in item for item in listed_memories))

        search = self.client.post(
            "/v1/adapters/openclaw/search",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.openclaw_agent_id,
                "query": "What stack should the pilot runtime use?",
                "limit": 5,
            },
        )
        self.assertEqual(search.status_code, 200)
        search_results = search.json()["results"]
        search_memories = [item["memory"] for item in search_results]
        self.assertTrue(any("dedicated memory worker" in item for item in search_memories))
        self.assertFalse(any("runbook acceptance checklist" in item for item in search_memories))
        self.assertEqual(
            sum(1 for item in search_memories if "dedicated memory worker" in item),
            1,
        )

    def test_bunkerai_management_contract_supports_full_memory_lifecycle(self) -> None:
        bootstrapped = self.client.post(
            "/v1/adapters/bunkerai/bootstrap",
            json={
                "namespace_name": "bunker:user:management-test",
                "agent_name": "bunker-core",
                "external_ref": "bunker:user:management-test",
            },
        )
        self.assertEqual(bootstrapped.status_code, 200)
        namespace_id = bootstrapped.json()["namespace_id"]
        agent_id = bootstrapped.json()["agent_id"]

        durable = self.client.post(
            "/v1/adapters/bunkerai/events",
            json={
                "namespace_id": namespace_id,
                "agent_id": agent_id,
                "session_id": "general",
                "event_type": "architecture_decision",
                "space_hint": "agent-core",
                "messages": [{"role": "user", "content": "The preferred deployment region is local-only."}],
                "metadata": {"source_message_id": "msg-durable"},
                "dedupe_key": "management-test:durable",
            },
        )
        local = self.client.post(
            "/v1/adapters/bunkerai/events",
            json={
                "namespace_id": namespace_id,
                "agent_id": agent_id,
                "session_id": "private-channel",
                "event_type": "conversation_turn",
                "space_hint": "session-space",
                "messages": [{"role": "user", "content": "Keep this note inside the private channel."}],
                "metadata": {"source_message_id": "msg-local"},
                "dedupe_key": "management-test:local",
            },
        )
        self.assertEqual(durable.status_code, 201)
        self.assertEqual(local.status_code, 201)
        self.assertGreaterEqual(WorkerRunner.run_pending_jobs(), 2)

        listed = self.client.get(
            f"/v1/adapters/bunkerai/memories?namespace_id={namespace_id}&agent_id={agent_id}&include_sessions=true"
        )
        self.assertEqual(listed.status_code, 200)
        memories = listed.json()["results"]
        self.assertTrue(any("local-only" in item["memory"] for item in memories))
        session_memory = next(item for item in memories if "private channel" in item["memory"])
        self.assertEqual(session_memory["metadata"]["session_id"], "private-channel")
        self.assertEqual(session_memory["metadata"]["source_message_id"], "msg-local")

        durable_memory = next(item for item in memories if "local-only" in item["memory"])
        self.assertEqual(durable_memory["metadata"]["source_message_id"], "msg-durable")
        corrected = self.client.patch(
            f"/v1/adapters/bunkerai/memories/{durable_memory['id']}?namespace_id={namespace_id}&agent_id={agent_id}",
            json={"content": "The preferred deployment region is an offline local node."},
        )
        self.assertEqual(corrected.status_code, 200)
        self.assertIn("offline local node", corrected.json()["memory"])

        deleted = self.client.delete(
            f"/v1/adapters/bunkerai/memories/{session_memory['id']}?namespace_id={namespace_id}&agent_id={agent_id}"
        )
        self.assertEqual(deleted.status_code, 204)

        purged = self.client.delete(
            f"/v1/adapters/bunkerai/memories?namespace_id={namespace_id}&agent_id={agent_id}"
        )
        self.assertEqual(purged.status_code, 204)
        empty = self.client.get(
            f"/v1/adapters/bunkerai/memories?namespace_id={namespace_id}&agent_id={agent_id}&include_sessions=true"
        )
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["results"], [])

    def test_bunkerai_delete_cancels_pending_consolidation(self) -> None:
        created = self.client.post(
            "/v1/adapters/bunkerai/events",
            json={
                "namespace_id": self.namespace_id,
                "agent_id": self.bunkerai_agent_id,
                "session_id": "temporary",
                "event_type": "conversation_turn",
                "space_hint": "session-space",
                "messages": [{"role": "user", "content": "Delete this before consolidation."}],
                "dedupe_key": "delete-before-consolidation",
            },
        )
        self.assertEqual(created.status_code, 201)
        episode_id = created.json()["event"]["episode_id"]

        deleted = self.client.delete(
            f"/v1/adapters/bunkerai/memories/{episode_id}?namespace_id={self.namespace_id}&agent_id={self.bunkerai_agent_id}"
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(WorkerRunner.run_pending_jobs(), 0)
