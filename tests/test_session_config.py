"""会话记住自己的配置（模型 / Web / 思考 / 知识库）。

设计：`development_doc/2026-09-15-session-config-design.md`

要点：
- `sessions` 新增 `config` 列（JSON）。
- 现有库用 `CREATE TABLE IF NOT EXISTS`，**没有列迁移机制** → 需要幂等的
  `ALTER TABLE ADD COLUMN`（已存在则跳过），否则老用户升级后该列不存在。
- 老会话读出空配置是 `{}`，前端落回默认 —— 平滑升级。
"""

from __future__ import annotations

import sqlite3

import pytest

from src.db.sessions import SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "sessions.db"))
    yield s
    conn = getattr(s._local, "conn", None)
    if conn:
        conn.close()


class TestSchemaMigration:
    def test_config_column_exists(self, store):
        conn = store._get_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        assert "config" in cols, "sessions 必须新增 config 列"

    def test_migration_is_idempotent(self, tmp_path):
        """重复初始化不能报错（老库升级路径会二次调用）。"""
        db = str(tmp_path / "again.db")
        SessionStore(db)._get_conn().close()
        SessionStore(db)._get_conn().close()  # 再开一次：ALTER 若不做幂等保护会炸

    def test_legacy_db_without_config_column_upgrades(self, tmp_path):
        """模拟升级前的库：没有 config 列，打开后应自动补上且数据不丢。"""
        db = str(tmp_path / "legacy.db")
        raw = sqlite3.connect(db)
        raw.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
                collections TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '', sources TEXT, metadata TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            """
        )
        raw.execute(
            "INSERT INTO sessions (id, title, collections, created_at, updated_at)"
            " VALUES ('legacy','老会话','[\"col_a\"]','t','t')"
        )
        raw.commit()
        raw.close()

        s = SessionStore(db)
        got = s.get_session("legacy")
        assert got is not None
        assert got.title == "老会话", "升级不能丢数据"
        assert got.collections == ["col_a"]
        assert got.config == {}, "老会话没有配置 → 空 dict，前端落回默认"
        conn = getattr(s._local, "conn", None)
        if conn:
            conn.close()


class TestConfigRoundTrip:
    def test_new_session_has_empty_config(self, store):
        s = store.create_session()
        assert s.config == {}

    def test_save_then_read_config(self, store):
        s = store.create_session()
        cfg = {
            "provider_id": "p1",
            "model": "m1",
            "web_search": True,
            "thinking": False,
            "collections": ["col_a"],
        }
        store.update_session(s.id, config=cfg)
        assert store.get_session(s.id).config == cfg

    def test_config_replaces_wholesale(self, store):
        """配置是整套替换，不是逐字段合并（避免删掉某项后旧值残留）。"""
        s = store.create_session()
        store.update_session(s.id, config={"model": "m1", "thinking": True})
        store.update_session(s.id, config={"model": "m2"})
        got = store.get_session(s.id).config
        assert got == {"model": "m2"}
        assert "thinking" not in got, "旧字段不应残留"

    def test_update_title_does_not_wipe_config(self, store):
        """标题更新（生成标题会调用）不能把配置抹掉。"""
        s = store.create_session()
        store.update_session(s.id, config={"model": "m1"})
        store.update_session(s.id, title="新标题")
        got = store.get_session(s.id)
        assert got.title == "新标题"
        assert got.config == {"model": "m1"}, "改标题不该动配置"

    def test_config_survives_reopen(self, tmp_path):
        db = str(tmp_path / "persist.db")
        s1 = SessionStore(db)
        sess = s1.create_session()
        s1.update_session(sess.id, config={"model": "m1"})
        conn = getattr(s1._local, "conn", None)
        if conn:
            conn.close()

        s2 = SessionStore(db)
        assert s2.get_session(sess.id).config == {"model": "m1"}
        conn2 = getattr(s2._local, "conn", None)
        if conn2:
            conn2.close()

    def test_corrupt_config_json_does_not_crash(self, store):
        """手工改坏 DB 也不能让整个会话接口炸掉。"""
        s = store.create_session()
        conn = store._get_conn()
        conn.execute("UPDATE sessions SET config = ? WHERE id = ?", ("{not json", s.id))
        conn.commit()
        got = store.get_session(s.id)
        assert got is not None
        assert got.config == {}, "坏 JSON 应降级为空配置而不是抛异常"
