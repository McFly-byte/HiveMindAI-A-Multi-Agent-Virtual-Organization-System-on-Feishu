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
            tags=["risk", "milestone"],
        )
        self.assertTrue(record.id.startswith("mem-"))
        fetched = self.store.get_memory(record.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.content, record.content)
        self.assertEqual(fetched.tags, ["risk", "milestone"])

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
            uploaded_by="alice",
        )
        self.assertGreaterEqual(info["chunk_count"], 1)
        results = self.store.search_documents(query="合规审计", corpus_id="proj-atlas-docs", top_k=3)
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("合规审计", results[0].content)


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
        memory.close()


if __name__ == "__main__":
    unittest.main()
