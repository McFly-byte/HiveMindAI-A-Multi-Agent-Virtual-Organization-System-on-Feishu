from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pmo_mvp.memory import MemoryStore, MemoryToolset, NullVectorBackend  # noqa: E402


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "memory.db"
        self.store = MemoryStore(db_path=self.db_path, vector_backend=NullVectorBackend())

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_write_and_get_memory(self) -> None:
        record = self.store.write_memory(
            content="项目 A 因依赖延期而错过里程碑。",
            agent_id="risk-assessor",
            memory_type="episodic",
            project_id="proj-A",
            run_id="run-A",
            tags=["risk", "milestone"],
            importance=1.5,
            metadata={"source": "unit-test"},
        )
        self.assertTrue(record.id.startswith("mem-"))
        fetched = self.store.get_memory(record.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.content, record.content)
        self.assertEqual(fetched.tags, ["risk", "milestone"])
        self.assertEqual(fetched.run_id, "run-A")
        self.assertEqual(fetched.importance, 1.5)
        self.assertEqual(fetched.metadata["source"], "unit-test")
        self.assertEqual(fetched.access_count, 1)

    def test_dedup_by_hash(self) -> None:
        a = self.store.write_memory(
            content="同样的内容", agent_id="x", memory_type="episodic"
        )
        b = self.store.write_memory(
            content="同样的内容", agent_id="x", memory_type="episodic"
        )
        self.assertEqual(a.id, b.id)

    def test_invalid_memory_type(self) -> None:
        with self.assertRaises(ValueError):
            self.store.write_memory(
                content="x", agent_id="a", memory_type="bogus"
            )

    def test_search_returns_relevant_memory(self) -> None:
        self.store.write_memory(
            content="资源协调失败导致项目延期",
            agent_id="coord",
            memory_type="episodic",
            project_id="proj-1",
        )
        self.store.write_memory(
            content="周报模板需要包含表情符号",
            agent_id="reporter",
            memory_type="procedural",
        )
        results = self.store.search_memories(query="资源协调", top_k=5)
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("资源协调", results[0].content)

    def test_search_filters(self) -> None:
        self.store.write_memory(
            content="proj-A 关键风险记录",
            agent_id="risk",
            memory_type="episodic",
            project_id="proj-A",
        )
        self.store.write_memory(
            content="proj-B 关键风险记录",
            agent_id="risk",
            memory_type="episodic",
            project_id="proj-B",
        )
        results = self.store.search_memories(query="关键风险", project_id="proj-A", top_k=5)
        self.assertTrue(all(r.project_id == "proj-A" for r in results))

    def test_memory_run_filter_weight_and_eviction(self) -> None:
        low = self.store.write_memory(
            content="依赖延期导致交付风险",
            agent_id="risk",
            memory_type="episodic",
            project_id="proj-A",
            run_id="run-low",
            importance=0.2,
            expires_at="2026-04-01 00:00:00",
        )
        high = self.store.write_memory(
            content="依赖延期导致交付风险需要升级处理",
            agent_id="risk",
            memory_type="episodic",
            project_id="proj-A",
            run_id="run-high",
            importance=2.0,
        )

        run_results = self.store.search_memories(
            query="依赖延期",
            project_id="proj-A",
            run_id="run-high",
            top_k=5,
        )
        self.assertEqual([item.id for item in run_results], [high.id])

        weighted = self.store.search_memories(
            query="依赖延期",
            project_id="proj-A",
            top_k=5,
        )
        self.assertEqual(weighted[0].id, high.id)

        deleted = self.store.evict_memories(
            project_id="proj-A",
            now="2026-05-01 00:00:00",
            min_importance=0.5,
        )
        self.assertIn(low.id, deleted)
        self.assertIsNone(self.store.get_memory(low.id))
        self.assertIsNotNone(self.store.get_memory(high.id))

    def test_point_memory_files(self) -> None:
        agent = self.store.write_agent_prompt(
            agent_id="risk-assessor",
            content="你是风险识别 Agent。",
        )
        project = self.store.write_project_profile(
            project_id="proj-A",
            content="# Project A\n\n稳定项目信息。",
        )
        self.assertTrue(agent.path.endswith("AGENT.md"))
        self.assertTrue(project.path.endswith("PROJECT.md"))
        self.assertIn("风险识别", self.store.read_agent_prompt("risk-assessor").content)
        self.assertIn("Project A", self.store.read_project_profile("proj-A").content)

    def test_sessions_process_events_and_project_context(self) -> None:
        session = self.store.start_session(
            agent_id="risk",
            run_id="run-123",
            project_id="proj-A",
            input_summary="scan project",
        )
        self.assertEqual(session.status, "running")
        finished = self.store.finish_session(
            run_id="run-123",
            output_summary="created one risk",
        )
        self.assertEqual(finished.status, "completed")
        self.assertIsNotNone(finished.ended_at)

        events = self.store.list_process_events(project_id="proj-A", run_id="run-123")
        self.assertGreaterEqual(len(events), 2)
        self.assertTrue(all(item.run_id == "run-123" for item in events))

        member = self.store.upsert_project_member(
            project_id="proj-A",
            name="赵敏",
            role="project_owner",
            responsibility="推进项目",
        )
        artifact = self.store.upsert_project_artifact(
            project_id="proj-A",
            artifact_type="base",
            name="项目台账",
            external_id="base-1",
        )
        context = self.store.get_project_context("proj-A")
        self.assertEqual(context["members"][0]["id"], member.id)
        self.assertEqual(context["artifacts"][0]["id"], artifact.id)

    def test_document_ingest_and_search(self) -> None:
        doc_path = Path(self.tmp.name) / "handbook.md"
        doc_path.write_text(
            "# 项目章程\n\n项目 Atlas 的核心目标是交付下一代财务报表系统。\n\n"
            "## 风险与约束\n\n关键约束是合规审计在 2026 年 6 月前必须通过。\n",
            encoding="utf-8",
        )
        info = self.store.ingest_document(
            file_path=doc_path,
            source_type="knowledge",
            corpus_id="proj-atlas-docs",
            project_id="proj-atlas",
            agent_id="doc-agent",
            run_id="run-doc",
            uploaded_by="alice",
        )
        self.assertGreaterEqual(info["chunk_count"], 1)
        results = self.store.search_documents(
            query="合规审计",
            corpus_id="proj-atlas-docs",
            project_id="proj-atlas",
            agent_id="doc-agent",
            run_id="run-doc",
            top_k=3,
        )
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("合规审计", results[0].content)
        self.assertEqual(results[0].project_id, "proj-atlas")


class ToolsetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "memory.db"
        self.store = MemoryStore(db_path=self.db_path, vector_backend=NullVectorBackend())
        self.tools = MemoryToolset(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_schemas_published(self) -> None:
        names = {s["name"] for s in self.tools.schemas()}
        self.assertEqual(
            names,
            {
                "memory_write",
                "memory_search",
                "memory_get",
                "memory_reflect",
                "profile_write",
                "profile_read",
                "session_start",
                "session_finish",
                "session_get",
                "session_list",
                "process_log",
                "process_search",
                "project_context_upsert",
                "project_context_get",
                "memory_weight_update",
                "memory_evict",
                "doc_ingest",
                "doc_search",
            },
        )

    def test_dispatch_unknown_tool(self) -> None:
        result = self.tools.dispatch("nope", {})
        self.assertIn("error", result)

    def test_write_then_search(self) -> None:
        write = self.tools.dispatch(
            "memory_write",
            {
                "content": "Risk Assessor flagged dependency risk",
                "agent_id": "risk-assessor",
                "memory_type": "episodic",
            },
        )
        self.assertTrue(write["ok"])
        search = self.tools.dispatch("memory_search", {"query": "dependency risk"})
        self.assertTrue(search["ok"])
        self.assertGreaterEqual(search["result"]["count"], 1)

    def test_reflect_creates_reflective_memory(self) -> None:
        for i in range(3):
            self.tools.dispatch(
                "memory_write",
                {
                    "content": f"事件 {i}: 资源紧张导致里程碑延期",
                    "agent_id": "risk-assessor",
                    "memory_type": "episodic",
                },
            )
        reflect = self.tools.dispatch(
            "memory_reflect",
            {"topic": "资源紧张", "agent_id": "risk-assessor"},
        )
        self.assertTrue(reflect["ok"])
        self.assertEqual(reflect["result"]["reflection"]["memory_type"], "reflective")
        self.assertGreaterEqual(reflect["result"]["source_count"], 1)


class EngineIntegrationTests(unittest.TestCase):
    def test_engine_runs_with_memory(self) -> None:
        from pmo_mvp.demo_data import build_demo_state
        from pmo_mvp.engine import PMOCycleEngine
        from pmo_mvp.store import JsonStateStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        runtime_path = Path(tmp.name) / "state.json"
        output_dir = Path(tmp.name) / "output"
        memory_db = Path(tmp.name) / "memory.db"

        store = JsonStateStore(runtime_path)
        store.save(build_demo_state())
        memory = MemoryStore(db_path=memory_db, vector_backend=NullVectorBackend())

        engine = PMOCycleEngine(store=store, output_dir=output_dir, memory=memory)
        summary = engine.run()
        self.assertIn("today", summary)

        memories = memory.list_memories(memory_type="episodic", limit=100)
        self.assertGreater(len(memories), 0, "expected episodic memories from agent runs")
        sessions = memory.list_sessions(limit=100)
        self.assertGreater(len(sessions), 0, "expected AgentSession records from agent runs")
        events = memory.list_process_events(limit=100)
        self.assertGreater(len(events), 0, "expected process events from agent runs")
        self.assertIsNotNone(memory.read_agent_prompt("risk-assessor"))
        self.assertIsNotNone(memory.read_project_profile("proj-atlas"))
        context = memory.get_project_context("proj-atlas")
        self.assertGreater(len(context["members"]), 0)
        memory.close()


if __name__ == "__main__":
    unittest.main()
