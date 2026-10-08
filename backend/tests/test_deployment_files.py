"""The committed deployment files keep the Phase 11 guarantees (P11.2; deployment.md).

Static checks over compose.production.yaml, the Caddyfile, the CI workflow and the drill
files: they are configuration, and a careless edit to any of them can hand a runtime
process the owner credential, publish images from a development branch or let a drill
revision into a release.
"""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.production.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

OWNER_SECRETS = {"LATTICE_MIGRATION_DATABASE_URL", "LATTICE_OWNER_PASSWORD"}
RUNTIME_SERVICES = {"api1", "api2", "worker", "web1", "web2", "caddy"}


def _services() -> dict[str, dict[str, Any]]:
    loaded: dict[str, Any] = yaml.safe_load(COMPOSE.read_text())
    services: dict[str, dict[str, Any]] = loaded["services"]
    return services


def _environment(service: dict[str, Any]) -> set[str]:
    env = service.get("environment") or {}
    return set(env) if isinstance(env, dict) else {item.split("=")[0] for item in env}


def test_the_owner_credential_reaches_only_the_migration_job_and_operators() -> None:
    services = _services()
    holders = {name for name, s in services.items() if _environment(s) & OWNER_SECRETS}
    assert holders == {"migrate", "ops", "bootstrap"}
    for name in RUNTIME_SERVICES:
        assert not _environment(services[name]) & OWNER_SECRETS, name
        assert "POSTGRES_PASSWORD" not in _environment(services[name]), name
        assert "PLATFORM_ADMIN_PASSWORD" not in _environment(services[name]), name


def test_master_keys_reach_only_the_api_and_operators() -> None:
    """P12.1 (C3.4): only the processes that present organization credentials hold the
    master keys -- never the worker, the migration job, the web or the database."""
    services = _services()
    keys = {"LATTICE_SECRET_KEYS", "LATTICE_SECRET_ACTIVE_KEY"}
    holders = {name for name, s in services.items() if _environment(s) & keys}
    assert holders == {"api1", "api2", "ops"}


def test_the_platform_administrator_is_used_only_by_the_bootstrap() -> None:
    services = _services()
    holders = {
        name
        for name, service in services.items()
        if "PLATFORM_ADMIN_PASSWORD" in str(service.get("environment", ""))
    }
    # postgres creates the role on an empty volume; bootstrap logs in with it.
    assert holders == {"postgres", "bootstrap"}
    bootstrap = services["bootstrap"]
    assert bootstrap["environment"]["PGUSER"] == "platform_admin"
    assert "bootstrap_production.sql" in " ".join(bootstrap["command"])


def test_each_runtime_process_gets_only_its_own_database_role() -> None:
    services = _services()
    for name in ("api1", "api2"):
        env = services[name]["environment"]
        assert env["LATTICE_DATABASE_URL"].startswith("postgresql+asyncpg://lattice_app:")
        assert "LATTICE_WORKER_DATABASE_URL" not in env
    worker = services["worker"]["environment"]
    assert worker["LATTICE_WORKER_DATABASE_URL"].startswith("postgresql+asyncpg://lattice_worker:")
    assert "LATTICE_DATABASE_URL" not in worker
    assert services["migrate"]["command"] == ["lattice", "migrate"]


def test_application_containers_are_hardened() -> None:
    services = _services()
    for name, service in services.items():
        if name == "postgres":
            continue
        assert service.get("read_only") is True, name
        assert service.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in service.get("security_opt", []), name


def test_the_ingress_has_a_maintenance_switch_for_the_api_only() -> None:
    caddyfile = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text()
    api_block = caddyfile[caddyfile.index("handle_path /api/*") : caddyfile.index("# Every other")]
    assert "try_files /api" in api_block
    assert "Retry-After" in api_block
    assert '"code":"service_unavailable"' in api_block
    assert "respond" in api_block
    assert "/srv/maintenance:ro" in COMPOSE.read_text()


def test_images_are_published_only_from_main_tags_or_dispatch() -> None:
    workflow: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text())
    jobs = workflow["jobs"]
    images = jobs["images"]
    condition = images["if"]
    assert "refs/heads/main" in condition
    assert "startsWith(github.ref, 'refs/tags/v')" in condition
    assert "workflow_dispatch" in condition
    assert images["needs"] == ["backend", "web", "dependency-audit"]
    # Only the images job may write packages; the workflow default stays read-only.
    assert workflow["permissions"] == {"contents": "read"}
    assert images["permissions"]["packages"] == "write"
    for name, job in jobs.items():
        if name != "images":
            assert "packages" not in job.get("permissions", {}), name
            # A manual publication never re-runs the test suites.
            assert job["if"] == "github.event_name != 'workflow_dispatch'", name
    steps = " ".join(str(step) for step in images["steps"])
    assert "status=success" in steps  # dispatch requires an earlier green push run
    assert "latest" not in steps


def test_drills_never_reach_a_release() -> None:
    assert not list((ROOT / "backend" / "alembic" / "versions").glob("*drill*"))
    assert "deploy/drills" not in WORKFLOW.read_text()
    dockerignore = (ROOT / "backend" / "Dockerfile.dockerignore").read_text()
    assert "deploy" not in dockerignore
