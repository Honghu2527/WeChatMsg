"""Compatibility wrapper for hardlink.db schema changes in newer WeChat 4.x builds.

The original WeChatMsg v4 parser hard-codes the 2025-era ``*_hardlink_info_v3``
table names.  Newer WeChat 4.1.x builds (including 4.1.13) use v4 hardlink
tables.  This wrapper resolves the table name at runtime and, most importantly,
turns a missing/changed hardlink table into a media-path fallback instead of
aborting the whole chat export.
"""

from __future__ import annotations

import sqlite3

from wxManager.log import logger
from .hardlink import HardLinkDB as _LegacyHardLinkDB


class HardLinkDB(_LegacyHardLinkDB):
    """HardLinkDB that supports both v3 and v4 hardlink table names."""

    _CANDIDATES = {
        "image": ("image_hardlink_info_v4", "image_hardlink_info_v3", "image_hardlink_info"),
        "video": ("video_hardlink_info_v4", "video_hardlink_info_v3", "video_hardlink_info"),
        "file": ("file_hardlink_info_v4", "file_hardlink_info_v3", "file_hardlink_info"),
    }

    def _resolve_table(self, kind: str) -> str | None:
        cache = getattr(self, "_resolved_hardlink_tables", None)
        if cache is None:
            cache = {}
            self._resolved_hardlink_tables = cache
        if kind in cache:
            return cache[kind]

        if not self.DB:
            cache[kind] = None
            return None

        cursor = self.DB.cursor()
        try:
            for table in self._CANDIDATES[kind]:
                cursor.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                    (table,),
                )
                if cursor.fetchone():
                    cache[kind] = table
                    return table
        except sqlite3.DatabaseError as exc:
            logger.warning("Unable to inspect hardlink.db schema for %s: %s", kind, exc)
        finally:
            cursor.close()

        cache[kind] = None
        return None

    def create_index(self):
        """Create optional md5 indexes only for tables that actually exist."""
        if not self.DB:
            return
        for kind in ("image", "video", "file"):
            table = self._resolve_table(kind)
            if not table:
                continue
            cursor = self.DB.cursor()
            try:
                cursor.execute(
                    f'CREATE INDEX IF NOT EXISTS "{table}_md5" ON "{table}"(md5);'
                )
                self.commit()
            except sqlite3.DatabaseError:
                # Index creation is an optimization only; export must continue.
                pass
            finally:
                cursor.close()

    def _get_by_md5(self, kind: str, md5: str):
        table = self._resolve_table(kind)
        if not table or not self.DB:
            return None

        # WeChat v3/v4 hardlink tables use the same fields needed by the
        # existing path builder.  If a future build changes them again, return
        # None so the caller can fall back to the time/chat based media path.
        join2 = "JOIN" if kind == "image" else "LEFT JOIN"
        join2_extra = "" if kind == "image" else " AND dir2 != 0"
        sql = f'''
        SELECT file_size, type, file_name,
               dir2id.username, dir2id2.username,
               {table}._rowid_, modify_time, extra_buffer
        FROM "{table}"
        JOIN dir2id ON dir2id.rowid = {table}.dir1
        {join2} dir2id AS dir2id2 ON dir2id2.rowid = {table}.dir2{join2_extra}
        WHERE {table}.md5 = ?
        '''
        cursor = self.DB.cursor()
        try:
            cursor.execute(sql, (md5,))
            return cursor.fetchone()
        except sqlite3.OperationalError as exc:
            logger.warning("Hardlink lookup skipped for %s (%s): %s", kind, table, exc)
            return None
        except sqlite3.DatabaseError as exc:
            logger.warning("Hardlink database lookup failed for %s: %s", kind, exc)
            return None
        finally:
            cursor.close()

    def get_image_by_md5(self, md5: str):
        return self._get_by_md5("image", md5)

    def get_video_by_md5(self, md5: str):
        return self._get_by_md5("video", md5)

    def get_file_by_md5(self, md5: str):
        return self._get_by_md5("file", md5)
