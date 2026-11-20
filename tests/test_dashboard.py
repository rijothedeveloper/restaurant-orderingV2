from pathlib import Path

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app


def client_for(dashboard_dir: Path) -> AsyncClient:
    settings = Settings(
        database_url=SecretStr("postgres://unused"), dashboard_dir=dashboard_dir
    )
    return AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://test"
    )


async def test_built_dashboard_is_served(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<title>Kitchen Dashboard</title>")
    (tmp_path / "_next").mkdir()
    (tmp_path / "_next" / "app.js").write_text("console.log(1)")

    async with client_for(tmp_path) as client:
        page = await client.get("/dashboard/")
        script = await client.get("/dashboard/_next/app.js")
        bare = await client.get("/dashboard")

    assert page.status_code == 200
    assert "Kitchen Dashboard" in page.text
    assert script.status_code == 200
    # The app's links are relative to /dashboard/, so the bare path redirects.
    assert bare.status_code == 307
    assert bare.headers["location"].endswith("/dashboard/")


async def test_app_starts_without_a_built_dashboard(tmp_path: Path) -> None:
    async with client_for(tmp_path / "missing") as client:
        response = await client.get("/dashboard/")

    assert response.status_code == 404
