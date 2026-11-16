#!/usr/bin/env python3
"""GCM v3.5 reusable internal-CNMT index reconciliation.

Only reidentifies previously unresolved, indexed, external
Switch NSPs. Does not install, transfer, or delete game files.
"""

import os
import sqlite3
import stat
from pathlib import Path


def reconcile(gcm):
    database = Path(gcm.DB_PATH)
    conn = sqlite3.connect(database, timeout=20)
    conn.row_factory = sqlite3.Row

    summary = {
        "examined": 0,
        "identified": 0,
        "unresolved": 0,
        "changed_on_disk": 0,
        "conflicts": 0,
        "errors": [],
    }

    try:
        records = conn.execute(
            """
            SELECT path, system, state, title_id,
                   application_id, content_type,
                   metadata_source, size, mtime
            FROM packages
            WHERE system = 'switch'
              AND state = 'external'
              AND LOWER(path) LIKE '%.nsp'
              AND (
                    COALESCE(application_id, '') = ''
                 OR COALESCE(title_id, '') = ''
                 OR COALESCE(content_type, '') NOT IN
                    ('update', 'dlc', 'application')
                 OR COALESCE(metadata_source, '') IN
                    ('', 'unverified')
              )
            ORDER BY path
            """
        ).fetchall()

        changes = []

        for row in records:
            path = Path(row["path"])
            summary["examined"] += 1

            try:
                before = path.stat()

                if not stat.S_ISREG(before.st_mode):
                    summary["unresolved"] += 1
                    continue

                if (
                    before.st_size != row["size"]
                    or abs(
                        before.st_mtime -
                        float(row["mtime"])
                    ) > 0.01
                ):
                    summary["changed_on_disk"] += 1
                    continue

                metadata = gcm.inspect_switch_candidate(
                    path, base_id=""
                )

                if (
                    metadata.get("metadata_source")
                    != "internal-cnmt"
                    or metadata.get("content_type")
                    not in ("update", "dlc")
                ):
                    summary["unresolved"] += 1
                    continue

                title_id = str(
                    metadata.get("title_id", "")
                ).upper()

                application_id = str(
                    metadata.get("application_id", "")
                ).upper()

                if (
                    len(title_id) != 16
                    or len(application_id) != 16
                    or not all(
                        c in "0123456789ABCDEF"
                        for c in title_id + application_id
                    )
                ):
                    summary["unresolved"] += 1
                    continue

                after = path.stat()

                if (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    summary["changed_on_disk"] += 1
                    continue

                changes.append(
                    (row, title_id, application_id,
                     metadata["content_type"])
                )

            except Exception as exc:
                summary["errors"].append(
                    {
                        "path": str(path),
                        "error": (
                            type(exc).__name__ + ": "
                            + str(exc)[:150]
                        ),
                    }
                )

        # Identification occurs before the database write.
        # Apply only records that still match their original state.
        if changes:
            conn.execute("BEGIN IMMEDIATE")

            try:
                for row, title_id, application_id, ctype in changes:
                    result = conn.execute(
                        """
                        UPDATE packages
                        SET title_id = ?,
                            application_id = ?,
                            content_type = ?,
                            metadata_source = 'internal-cnmt'
                        WHERE path = ?
                          AND system = 'switch'
                          AND state = 'external'
                          AND COALESCE(title_id, '') = ?
                          AND COALESCE(application_id, '') = ?
                          AND COALESCE(content_type, '') = ?
                          AND COALESCE(metadata_source, '') = ?
                          AND size = ?
                          AND mtime = ?
                        """,
                        (
                            title_id,
                            application_id,
                            ctype,
                            row["path"],
                            row["title_id"] or "",
                            row["application_id"] or "",
                            row["content_type"] or "",
                            row["metadata_source"] or "",
                            row["size"],
                            row["mtime"],
                        ),
                    )

                    if result.rowcount == 1:
                        summary["identified"] += 1
                    else:
                        summary["conflicts"] += 1

                conn.commit()

            except Exception:
                conn.rollback()
                raise

        return summary

    finally:
        conn.close()
