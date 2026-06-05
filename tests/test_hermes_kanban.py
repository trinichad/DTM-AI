"""hermes_kanban tests — read the delegation board (real schema), grouping, task detail, write guard."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from execution.core import hermes_kanban as K


class StubCfg:
    def __init__(self, d): self.d = d
    def get(self, k, default=None): return self.d.get(k, default)


# Minimal slice of Hermes' real kanban schema (the columns the reader touches).
_SCHEMA = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, assignee TEXT, status TEXT NOT NULL,
    priority INTEGER DEFAULT 0, created_by TEXT, created_at INTEGER NOT NULL,
    started_at INTEGER, completed_at INTEGER, workspace_kind TEXT DEFAULT 'scratch',
    tenant TEXT, result TEXT, consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_failure_error TEXT, current_run_id INTEGER, model_override TEXT,
    goal_mode INTEGER NOT NULL DEFAULT 0);
CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, author TEXT,
    body TEXT, created_at INTEGER);
CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, profile TEXT,
    status TEXT, outcome TEXT, summary TEXT, error TEXT, started_at INTEGER, ended_at INTEGER);
CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, run_id INTEGER,
    kind TEXT, payload TEXT, created_at INTEGER);
CREATE TABLE task_links (parent_id TEXT, child_id TEXT, PRIMARY KEY (parent_id, child_id));
"""


def seed(db: Path):
    c = sqlite3.connect(db)
    c.executescript(_SCHEMA)
    c.execute("INSERT INTO tasks(id,title,assignee,status,created_at,result) VALUES"
              "('t1','Patch review','sentinelops','running',1700000000,NULL)")
    c.execute("INSERT INTO tasks(id,title,assignee,status,created_at,completed_at,result) VALUES"
              "('t2','MFA audit','tenantsmith','done',1700000100,1700000200,'17 users')")
    c.execute("INSERT INTO tasks(id,title,assignee,status,created_at) VALUES"
              "('t3','Triage me','default','triage',1700000300)")
    c.execute("INSERT INTO tasks(id,title,assignee,status,created_at) VALUES"
              "('t4','Old archived','sentinelops','archived',1699990000)")
    c.execute("INSERT INTO task_comments(task_id,author,body,created_at) VALUES"
              "('t2','tenantsmith','found 17 gaps',1700000150)")
    c.execute("INSERT INTO task_runs(task_id,profile,status,outcome,summary,started_at,ended_at) "
              "VALUES('t2','tenantsmith','done','completed','Audited MFA',1700000110,1700000200)")
    c.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES"
              "('t2','completed','{}',1700000200)")
    c.execute("INSERT INTO task_links(parent_id,child_id) VALUES('t3','t1')")
    c.commit(); c.close()


class Kanban(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        seed(self.d / "kanban.db")
        self.cfg = StubCfg({"DTM_HERMES_DATA_DIR": str(self.d)})

    def tearDown(self):
        self.tmp.cleanup()

    def test_available(self):
        self.assertTrue(K.available(self.cfg))
        self.assertFalse(K.available(StubCfg({"DTM_HERMES_DATA_DIR": str(self.d / "nope")})))

    def test_list_hides_archived(self):
        ids = {t["id"] for t in K.list_tasks(self.cfg)}
        self.assertEqual(ids, {"t1", "t2", "t3"})
        self.assertIn("t4", {t["id"] for t in K.list_tasks(self.cfg, include_archived=True)})

    def test_board_columns_and_counts(self):
        b = K.board(self.cfg)
        self.assertTrue(b["available"])
        self.assertEqual(b["total"], 3)
        col = {c["status"]: c["tasks"] for c in b["columns"]}
        self.assertEqual([t["id"] for t in col["running"]], ["t1"])
        self.assertEqual([t["id"] for t in col["done"]], ["t2"])
        self.assertEqual([t["id"] for t in col["triage"]], ["t3"])
        self.assertEqual(b["counts"]["running"], 1)
        self.assertEqual(b["by_assignee"]["sentinelops"], 1)  # archived t4 excluded
        # columns appear in board order
        self.assertEqual([c["status"] for c in b["columns"]], K.BOARD_ORDER)

    def test_ms_normalization(self):
        t = next(t for t in K.list_tasks(self.cfg) if t["id"] == "t1")
        self.assertEqual(t["created_ms"], 1700000000 * 1000)   # seconds → ms

    def test_latest_run_summary_attached(self):
        # workers often answer in the run summary, not the result column — board needs it on the card
        t2 = next(t for t in K.list_tasks(self.cfg) if t["id"] == "t2")
        self.assertEqual(t2["latest_summary"], "Audited MFA")
        t1 = next(t for t in K.list_tasks(self.cfg) if t["id"] == "t1")
        self.assertIsNone(t1["latest_summary"])                # no runs yet

    def test_get_task_detail(self):
        t = K.get_task("t2", self.cfg)
        self.assertEqual(t["result"], "17 users")
        self.assertEqual(len(t["comments"]), 1)
        self.assertEqual(t["runs"][0]["summary"], "Audited MFA")
        self.assertEqual(t["runs"][0]["outcome"], "completed")
        self.assertEqual(t["events"][0]["kind"], "completed")
        self.assertEqual(t["parents"], [])
        # t3 is parent of t1
        self.assertEqual(K.get_task("t3", self.cfg)["children"], ["t1"])
        self.assertIsNone(K.get_task("nope", self.cfg))

    def test_unavailable_is_safe(self):
        empty = StubCfg({"DTM_HERMES_DATA_DIR": str(self.d / "nope")})
        self.assertEqual(K.list_tasks(empty), [])
        self.assertFalse(K.board(empty)["available"])
        self.assertIsNone(K.get_task("t1", empty))

    def test_create_requires_title(self):
        with self.assertRaises(ValueError):
            K.create_task("   ", cfg=self.cfg)

    def test_create_rejects_bad_assignee(self):
        with self.assertRaises(ValueError):
            K.create_task("x", assignee="../evil", cfg=self.cfg)

    def test_write_fails_closed_without_wrapper(self):
        # wrapper path points nowhere → delegation must raise, never silently no-op
        orig = K._WRAPPER
        K._WRAPPER = str(self.d / "no-such-wrapper.sh")
        try:
            with self.assertRaises(K.KanbanError):
                K.create_task("real title", assignee="sentinelops", cfg=self.cfg)
            with self.assertRaises(K.KanbanError):
                K.dispatch(self.cfg)
            with self.assertRaises(K.KanbanError):
                K.assign_task("t1", "tenantsmith", self.cfg)
        finally:
            K._WRAPPER = orig


if __name__ == "__main__":
    unittest.main()
