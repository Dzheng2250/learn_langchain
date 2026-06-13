"""Content-addressed local storage for large durable payloads."""

import hashlib
import os
import zlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from src.config.paths import artifact_dir
from src.core.state.database import LocalStateDatabase


@dataclass(frozen=True)
class ArtifactRecord:
    """Metadata returned after storing one bounded content artifact."""

    artifact_id: str
    sha256: str
    byte_size: int
    relative_path: str


class ArtifactStore:
    """Deduplicate compressed artifacts and retain them while referenced."""

    def __init__(
        self,
        database: LocalStateDatabase,
        root: str | Path | None = None,
        *,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.database = database
        self.root = Path(root or artifact_dir()).expanduser().resolve()
        self.max_bytes = max(1, int(max_bytes))

    def put(self, content: str | bytes, *, content_type: str = "text/plain") -> ArtifactRecord:
        """Store bounded content once by SHA-256 and return durable metadata."""
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        raw = raw[: self.max_bytes]
        digest = hashlib.sha256(raw).hexdigest()
        relative = f"{digest[:2]}/{digest}.zlib"
        target = self.root / relative
        self.root.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT artifact_id, byte_size, relative_path FROM artifacts WHERE sha256 = ?",
                (digest,),
            ).fetchone()
            if row:
                return ArtifactRecord(row["artifact_id"], digest, row["byte_size"], row["relative_path"])
            artifact_id = uuid4().hex
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(zlib.compress(raw))
            os.replace(temporary, target)
            conn.execute(
                """
                INSERT INTO artifacts(artifact_id, sha256, content_type, byte_size, relative_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (artifact_id, digest, content_type, len(raw), relative),
            )
        return ArtifactRecord(artifact_id, digest, len(raw), relative)

    def add_reference(self, artifact_id: str, owner_type: str, owner_id: str) -> None:
        """Protect an artifact while one durable owner references it."""
        with self.database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifact_references(artifact_id, owner_type, owner_id)
                VALUES (?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                (artifact_id, owner_type, owner_id),
            )

    def collect_garbage(self) -> int:
        """Delete unreferenced artifacts only when maintenance is explicit."""
        with self.database.transaction() as conn:
            rows = conn.execute(
                """
                SELECT artifact_id, relative_path FROM artifacts
                WHERE NOT EXISTS (
                    SELECT 1 FROM artifact_references r
                    WHERE r.artifact_id = artifacts.artifact_id
                )
                """
            ).fetchall()
            for row in rows:
                (self.root / row["relative_path"]).unlink(missing_ok=True)
                conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (row["artifact_id"],))
        return len(rows)
