import sqlite3
import tempfile
import unittest
from pathlib import Path

from tw_day_trading_lab.storage import (
    get_schema_version,
    apply_migration
)

class TestDBMigration(unittest.TestCase):
    """Sprint 4-C: TiDB schema migration tests using SQLite compatibility layer."""

    def setUp(self):
        # 使用 SQLite 記憶體資料庫來測試 SQL
        self.connection = sqlite3.connect(":memory:")

    def tearDown(self):
        self.connection.close()

    def test_get_schema_version_empty_db(self):
        """Empty database without schema_migrations table returns version 0."""
        version = get_schema_version(self.connection)
        self.assertEqual(version, 0)

    def test_apply_migration_and_track_version(self):
        """Applying migration creates table and records version."""
        # 1. 建立 schema_migrations 表
        migration_sql_1 = """
        CREATE TABLE schema_migrations (
          version INT PRIMARY KEY,
          applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          description TEXT
        );
        """
        apply_migration(self.connection, 1, migration_sql_1, "init_schema_migrations")
        
        # 檢查 version 應為 1
        self.assertEqual(get_schema_version(self.connection), 1)

        # 2. 執行第二個 migration
        migration_sql_2 = """
        CREATE TABLE test_table (
          id INT PRIMARY KEY,
          name TEXT
        );
        """
        apply_migration(self.connection, 2, migration_sql_2, "create_test_table")
        
        # 檢查 version 應升到 2
        self.assertEqual(get_schema_version(self.connection), 2)

        # 3. 確認 test_table 存在
        cursor = self.connection.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "test_table")
        cursor.close()

    def test_cmd_db_migrate_flow(self):
        """Test cmd_db_migrate command logic (mocking argparse)."""
        import argparse
        from unittest.mock import patch, MagicMock
        from tw_day_trading_lab.cli import cmd_db_migrate

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            
            # 建立假的 migration 檔案
            # 001_init.sql: 建立 schema_migrations 表
            m1 = tmpdir / "001_init.sql"
            m1.write_text("""
            CREATE TABLE schema_migrations (
              version INT PRIMARY KEY,
              applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              description TEXT
            );
            """, encoding="utf-8")

            # 002_add_table.sql: 建立 test_table 表
            m2 = tmpdir / "002_add_table.sql"
            m2.write_text("""
            CREATE TABLE users (
              id INT PRIMARY KEY,
              username TEXT
            );
            """, encoding="utf-8")

            args = argparse.Namespace(schema_dir=str(tmpdir))

            class MockSqliteConnection(MagicMock):
                pass

            mock_conn = MockSqliteConnection()
            mock_conn.cursor.side_effect = self.connection.cursor
            mock_conn.commit.side_effect = self.connection.commit
            mock_conn.rollback.side_effect = self.connection.rollback

            with patch("tw_day_trading_lab.cli.connect_tidb", return_value=mock_conn):
                with patch("tw_day_trading_lab.cli.TiDBConfig") as mock_config:
                    mock_cfg_inst = MagicMock()
                    mock_cfg_inst.database = "test_db"
                    mock_config.from_env.return_value = mock_cfg_inst

                    # 執行 db migrate
                    cmd_db_migrate(args)

                    # 執行完後，版本應該是 2
                    self.assertEqual(get_schema_version(self.connection), 2)

                    # 二次執行 db migrate，不應再套用
                    with patch("builtins.print") as mock_print:
                        cmd_db_migrate(args)
                        # 會印出 "No pending migrations to apply."
                        mock_print.assert_any_call("No pending migrations to apply.")


if __name__ == "__main__":
    unittest.main()
