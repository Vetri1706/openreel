import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .models import Asset, Project, now


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

    def new_job(self, job: dict):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            active = [json.loads(row[0]) for row in c.execute("SELECT data FROM jobs")]
            if sum(j["status"] in ("queued", "rendering") and self.alive(j) for j in active) >= 4:
                raise ValueError("Render queue is full. Wait for an export to finish.")
            c.execute("INSERT INTO jobs VALUES (?, ?)", (job["id"], json.dumps(job)))

    @staticmethod
    def alive(job: dict) -> bool:
        try:
            os.kill(job["pid"], 0)
            return True
        except OSError:
            return False

    def job(self, job_id: str) -> dict:
        with self.connect() as c:
            row = c.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("Render not found")
        data = json.loads(row[0])
        if data["status"] in ("queued", "rendering") and not self.alive(data):
            return self.update_job(job_id, status="failed", error="Renderer stopped. Start a new export.")
        return data

    def update_job(self, job_id: str, **changes) -> dict:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError("Render not found")
            data = json.loads(row[0])
            data.update(changes)
            c.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(data), job_id))
        return data

    def jobs(self, project_id: str) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT id,data FROM jobs ORDER BY rowid DESC").fetchall()
        return [self.job(row[0]) for row in rows if json.loads(row[1])["project_id"] == project_id]

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
