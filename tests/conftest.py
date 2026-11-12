import pytest
from fastapi.testclient import TestClient

from openreel.service import Service
from openreel.web import create_app


@pytest.fixture
def service(tmp_path):
    service = Service(tmp_path / "data", tmp_path / "imports")
    service.import_root.mkdir()
    yield service
    service.renderer.pool.shutdown(wait=True)


@pytest.fixture
def client(service):
    with TestClient(
        create_app(service), base_url="http://localhost:8787", headers={"X-OpenReel-Request": "studio"}
    ) as client:
        yield client
