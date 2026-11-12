import hmac
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .config import limits
from .models import Asset, Project, now, uid


class Conflict(ValueError):
    pass


class Store:
    """SQLite transactions coordinate studio and independent stdio processes."""

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("assets", "renders", "scratch"):
            (self.root / name).mkdir(exist_ok=True)
        self.db = self.root / "openreel.sqlite3"
        with self.connect() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS auth (
                    kind TEXT, key TEXT, data TEXT, expires REAL,
                    PRIMARY KEY (kind, key)
                );
            """)
        os.chmod(self.db, 0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db, timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, project: Project) -> Project:
        with self.connect() as c:
            c.execute("INSERT INTO projects VALUES (?, ?)", (project.id, project.model_dump_json()))
        return project

    def get(self, project_id: str) -> Project:
        with self.connect() as c:
            row = c.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError("Project not found")
        return Project.model_validate_json(row[0])

    def projects(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT data FROM projects").fetchall()
        projects = [json.loads(row[0]) for row in rows]
        return sorted(projects, key=lambda p: p["updated_at"], reverse=True)

    def save(self, project: Project) -> Project:
        project = Project.model_validate(project.model_dump())
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT data FROM projects WHERE id=?", (project.id,)).fetchone()
            if not row:
                raise KeyError("Project not found")
            old = Project.model_validate_json(row[0])
            if old.revision != project.revision:
                raise Conflict("This project changed elsewhere. Reload before saving.")
            self.validate_assets(project)
            project.revision += 1
            project.created_at = old.created_at
            project.updated_at = now()
            c.execute("UPDATE projects SET data=? WHERE id=?", (project.model_dump_json(), project.id))
        return project

    def validate_assets(self, project: Project):
        refs = [(s.asset_id, {"image", "video"}) for s in project.scenes]
        refs += [(s.audio_asset_id, {"audio"}) for s in project.scenes]
        refs += [(c.reference_asset_id, {"image"}) for c in project.characters]
        refs += [(project.music_asset_id, {"audio"})]
        for asset_id, kinds in refs:
            if asset_id:
                asset = self.asset(asset_id)
                if asset.project_id != project.id or asset.kind not in kinds:
                    raise ValueError("Asset belongs to another project or has the wrong media type.")

    def add_asset(self, asset: Asset):
        with self.connect() as c:
            c.execute("INSERT INTO assets VALUES (?, ?)", (asset.id, asset.model_dump_json()))

    def asset(self, asset_id: str) -> Asset:
        with self.connect() as c:
            row = c.execute("SELECT data FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            raise KeyError("Asset not found")
        return Asset.model_validate_json(row[0])

    def assets(self, project_id: str) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT data FROM assets").fetchall()
        return [a for row in rows if (a := json.loads(row[0]))["project_id"] == project_id]

    def asset_path(self, asset_id: str) -> Path:
        return self.root / "assets" / self.asset(asset_id).filename

    @staticmethod
    def public_job(data: dict) -> dict:
        return {key: value for key, value in data.items() if key not in {"project_snapshot", "lease_token"}}

    def new_job(self, job: dict):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            active = [json.loads(row[0]) for row in c.execute("SELECT data FROM jobs")]
            if sum(j["status"] in ("queued", "rendering") for j in active) >= limits().max_queued_renders:
                raise ValueError("Render queue is full. Wait for an export to finish.")
            c.execute("INSERT INTO jobs VALUES (?, ?)", (job["id"], json.dumps(job)))

    def job(self, job_id: str) -> dict:
        with self.connect() as c:
            row = c.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("Render not found")
        return self.public_job(json.loads(row[0]))

    def claim_job(self, worker_id: str, lease_seconds: float = 90) -> dict | None:
        """Atomically claim the oldest pending job, including an expired lease.

        A snapshot and opaque lease token are private worker data. They are never
        exposed by job()/jobs(). Workers must heartbeat before the lease expires.
        """
        if not worker_id or len(worker_id) > 200 or not 5 <= lease_seconds <= 3600:
            raise ValueError("Invalid worker ID or lease duration.")
        clock = time.time()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute("SELECT id,data FROM jobs ORDER BY rowid").fetchall()
            for job_id, raw in rows:
                data = json.loads(raw)
                if data["status"] not in {"queued", "rendering"}:
                    continue
                if data["status"] == "rendering" and data.get("lease_expires", 0) > clock:
                    continue
                if data.get("cancel"):
                    data.update(status="canceled", lease_token=None, lease_expires=0)
                    c.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(data), job_id))
                    continue
                if "project_snapshot" not in data:
                    # Old versions did not persist snapshots, so cannot safely resume them.
                    data.update(
                        status="failed", error="Legacy export has no saved snapshot. Start a new export."
                    )
                    c.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(data), job_id))
                    continue
                data.update(
                    status="rendering",
                    progress=1,
                    worker_id=worker_id,
                    lease_token=uid(),
                    lease_expires=clock + lease_seconds,
                    attempts=data.get("attempts", 0) + 1,
                    error=None,
                )
                c.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(data), job_id))
                return data
        return None

    def leased_job(self, job_id: str, lease_token: str) -> dict:
        with self.connect() as c:
            row = c.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("Render not found")
        data = json.loads(row[0])
        if (
            data["status"] != "rendering"
            or not lease_token
            or not hmac.compare_digest(data.get("lease_token") or "", lease_token)
            or data.get("lease_expires", 0) <= time.time()
        ):
            raise Conflict("Render lease expired or belongs to another worker.")
        return data

    def heartbeat_job(self, job_id: str, lease_token: str, lease_seconds: float = 90) -> bool:
        if not 5 <= lease_seconds <= 3600:
            raise ValueError("Invalid lease duration.")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return False
            data = json.loads(row[0])
            if (
                data["status"] != "rendering"
                or data.get("lease_token") != lease_token
                or data.get("lease_expires", 0) <= time.time()
            ):
                return False
            data["lease_expires"] = time.time() + lease_seconds
            c.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(data), job_id))
            return True

    def update_leased_job(self, job_id: str, lease_token: str, **changes) -> dict:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError("Render not found")
            data = json.loads(row[0])
            if (
                data["status"] != "rendering"
                or data.get("lease_token") != lease_token
                or data.get("lease_expires", 0) <= time.time()
            ):
                raise Conflict("Render lease expired or belongs to another worker.")
            if data.get("cancel") and changes.get("status") == "completed":
                raise Conflict("Render was canceled before completion.")
            data.update(changes)
            if data["status"] in {"completed", "failed", "canceled"}:
                data.update(lease_token=None, lease_expires=0)
            c.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(data), job_id))
        return self.public_job(data)

    def cancel_job(self, job_id: str) -> dict:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError("Render not found")
            data = json.loads(row[0])
            if data["status"] in {"queued", "rendering"}:
                data["cancel"] = True
                if data["status"] == "queued" or data.get("lease_expires", 0) <= time.time():
                    data.update(status="canceled", lease_token=None, lease_expires=0)
                c.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(data), job_id))
        return self.public_job(data)

    def update_job(self, job_id: str, **changes) -> dict:
        """Administrative update; render workers must use update_leased_job."""
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError("Render not found")
            data = json.loads(row[0])
            data.update(changes)
            c.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(data), job_id))
        return self.public_job(data)

    def jobs(self, project_id: str) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT data FROM jobs ORDER BY rowid DESC").fetchall()
        return [
            self.public_job(data) for row in rows if (data := json.loads(row[0]))["project_id"] == project_id
        ]

    def auth_put(self, kind: str, key: str, data: dict, expires: float):
        with self.connect() as c:
            c.execute("DELETE FROM auth WHERE expires < ?", (time.time(),))
            count = c.execute("SELECT count(*) FROM auth").fetchone()[0]
            if count > 2000:
                raise ValueError("Authorization store is full. Try again later.")
            c.execute("INSERT OR REPLACE INTO auth VALUES (?,?,?,?)", (kind, key, json.dumps(data), expires))

    def auth_get(self, kind: str, key: str, pop: bool = False) -> dict | None:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT data,expires FROM auth WHERE kind=? AND key=?", (kind, key)).fetchone()
            if pop:
                c.execute("DELETE FROM auth WHERE kind=? AND key=?", (kind, key))
        return json.loads(row[0]) if row and row[1] > time.time() else None
