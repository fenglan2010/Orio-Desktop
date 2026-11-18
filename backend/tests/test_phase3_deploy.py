"""Phase III increment 4: production guards and deploy manifests (no live Docker/cloud)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import (
    DEMO_JWT_SECRET,
    ProductionConfigError,
    Settings,
    clear_settings_cache,
    validate_runtime_settings,
)
from app.database import reset_engine
from app.gateway.router import reset_gateway

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
K8S_ROOT = PROJECT_ROOT / "deploy" / "k8s"
SAFE_JWT = "unit-test-production-jwt-not-for-deploy"
FAKE_DB = "postgresql+psycopg://oiro:super-secret-pass@db.internal:5432/oiro?sslmode=require"


def test_development_allows_demo_jwt_and_sqlite():
    settings = Settings(
        oiro_env="development",
        jwt_secret=DEMO_JWT_SECRET,
        database_url="sqlite:////tmp/oiro-deploy-test.db",
    )
    returned = validate_runtime_settings(settings)
    assert returned is settings


def test_production_rejects_demo_jwt_secret():
    settings = Settings(
        oiro_env="production",
        jwt_secret=DEMO_JWT_SECRET,
        database_url=FAKE_DB,
    )
    with pytest.raises(ProductionConfigError, match="JWT_SECRET") as excinfo:
        validate_runtime_settings(settings)
    message = str(excinfo.value)
    assert DEMO_JWT_SECRET not in message
    assert "super-secret-pass" not in message
    assert "postgresql+psycopg://" not in message


def test_production_rejects_empty_jwt_secret():
    settings = Settings(oiro_env="prod", jwt_secret="   ", database_url=FAKE_DB)
    with pytest.raises(ProductionConfigError, match="JWT_SECRET"):
        validate_runtime_settings(settings)


def test_production_rejects_sqlite_and_does_not_fallback():
    settings = Settings(
        oiro_env="production",
        jwt_secret=SAFE_JWT,
        database_url="sqlite:////tmp/oiro-should-not-be-used.db",
    )
    with pytest.raises(ProductionConfigError, match="SQLite") as excinfo:
        validate_runtime_settings(settings)
    message = str(excinfo.value)
    assert "oiro-should-not-be-used.db" not in message
    assert SAFE_JWT not in message


def test_production_accepts_custom_jwt_and_postgres_url():
    settings = Settings(oiro_env="production", jwt_secret=SAFE_JWT, database_url=FAKE_DB)
    validate_runtime_settings(settings)


def test_production_startup_rejects_demo_jwt(monkeypatch, tmp_path):
    monkeypatch.setenv("OIRO_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", DEMO_JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", FAKE_DB)
    clear_settings_cache()
    reset_engine()
    reset_gateway()
    from app.main import app

    with pytest.raises(ProductionConfigError, match="JWT_SECRET") as excinfo:
        with TestClient(app):
            pass
    assert DEMO_JWT_SECRET not in str(excinfo.value)
    assert "super-secret-pass" not in str(excinfo.value)
    clear_settings_cache()
    reset_engine()
    reset_gateway()


def test_compose_and_k8s_files_exist():
    assert COMPOSE_FILE.is_file()
    assert (PROJECT_ROOT / "deploy" / "docker" / "Dockerfile.api").is_file()
    assert (PROJECT_ROOT / "deploy" / "docker" / "Dockerfile.frontend").is_file()
    assert (PROJECT_ROOT / "deploy" / "docker" / "nginx.conf").is_file()
    assert (PROJECT_ROOT / "deploy" / "compose.env.example").is_file()
    assert (K8S_ROOT / "base" / "kustomization.yaml").is_file()
    assert (K8S_ROOT / "base" / "secret.yaml.example").is_file()
    assert (K8S_ROOT / "overlays" / "tke" / "kustomization.yaml").is_file()
    assert (K8S_ROOT / "overlays" / "ack" / "kustomization.yaml").is_file()
    assert (K8S_ROOT / "lab" / "postgres.yaml").is_file()
    assert (K8S_ROOT / "lab" / "redis.yaml").is_file()


def _load_yaml(path: Path) -> list[object]:
    docs = list(yaml.safe_load_all(path.read_text()))
    return [doc for doc in docs if doc is not None]


def test_compose_file_parses_and_stays_localhost():
    docs = _load_yaml(COMPOSE_FILE)
    assert len(docs) == 1
    compose = docs[0]
    services = compose["services"]
    assert set(services) >= {"frontend", "api", "postgres", "redis", "worker", "beat"}

    for name in ("postgres", "redis"):
        ports = services[name].get("ports") or []
        assert ports == [], f"{name} must not publish host ports"

    for name in ("frontend", "api"):
        ports = services[name]["ports"]
        assert ports, f"{name} should publish a localhost port"
        for mapping in ports:
            text = mapping if isinstance(mapping, str) else str(mapping)
            assert text.startswith("127.0.0.1:"), f"{name} port must bind localhost only: {text}"
            assert "0.0.0.0" not in text

    blob = COMPOSE_FILE.read_text()
    assert "OVERSEAS_ENABLED: \"false\"" in blob
    assert "CTRIP_WRITE_ENABLED: \"false\"" in blob
    assert "MEITUAN_WRITE_ENABLED: \"false\"" in blob
    assert "FLIGGY_WRITE_ENABLED: \"false\"" in blob
    assert "oiro-demo-secret-change-me" not in blob
    assert "OIRO_ENV: ${OIRO_ENV:-production}" in blob

    example = (PROJECT_ROOT / "deploy" / "compose.env.example").read_text()
    assert "oiro-demo-secret-change-me" not in example.split("JWT_SECRET", 1)[-1].splitlines()[0]
    assert "JWT_SECRET=" in example
    assert "POSTGRES_PASSWORD=" in example


def _walk_k8s() -> list[tuple[Path, dict]]:
    found: list[tuple[Path, dict]] = []
    for path in sorted(K8S_ROOT.rglob("*")):
        if path.suffix not in {".yaml", ".yml"}:
            continue
        for doc in _load_yaml(path):
            assert isinstance(doc, dict), f"{path} produced a non-mapping document"
            found.append((path, doc))
    assert found, "expected Kubernetes manifests"
    return found


def test_k8s_manifests_parse_and_are_hardened():
    docs = _walk_k8s()
    kinds = {(path.name, doc.get("kind")) for path, doc in docs}
    assert ("api.yaml", "Deployment") in kinds
    assert ("api.yaml", "Service") in kinds
    assert ("frontend.yaml", "Deployment") in kinds
    assert ("worker.yaml", "Deployment") in kinds
    assert ("beat.yaml", "Deployment") in kinds
    assert ("ingress.yaml", "Ingress") in kinds

    for path, doc in docs:
        kind = doc.get("kind")
        if kind == "Deployment":
            template = doc["spec"]["template"]["spec"]
            pod_ctx = template.get("securityContext") or {}
            name = doc["metadata"]["name"]
            if name.startswith("oiro-lab-"):
                # Lab postgres image runs as the upstream postgres user.
                assert pod_ctx.get("seccompProfile", {}).get("type") == "RuntimeDefault"
            else:
                assert pod_ctx.get("runAsNonRoot") is True, name
            for container in template["containers"]:
                ctx = container.get("securityContext") or {}
                assert ctx.get("allowPrivilegeEscalation") is False, name
                assert ctx.get("privileged") is False, name
                dropped = (ctx.get("capabilities") or {}).get("drop") or []
                assert "ALL" in dropped, name
            if name in {"oiro-api", "oiro-frontend"}:
                probes = template["containers"][0]
                assert "readinessProbe" in probes
                assert "livenessProbe" in probes
        if kind == "Service":
            spec = doc.get("spec") or {}
            assert spec.get("type", "ClusterIP") == "ClusterIP", path
        if kind == "Deployment":
            pod_spec = doc["spec"]["template"]["spec"]
            assert pod_spec.get("hostNetwork") in {None, False}
            for container in pod_spec["containers"]:
                assert "privileged: true" not in yaml.safe_dump(container)

    kustomization = yaml.safe_load((K8S_ROOT / "base" / "kustomization.yaml").read_text())
    image_names = {item["name"] for item in kustomization["images"]}
    assert {"oiro-api", "oiro-frontend"} <= image_names

    secret_example = (K8S_ROOT / "base" / "secret.yaml.example").read_text()
    assert "REPLACE_WITH_LONG_RANDOM_SECRET" in secret_example
    assert "oiro-demo-secret-change-me" not in secret_example
    assert "sslmode=require" in secret_example
    assert "PRIVATE_HOST" in secret_example

    lab_readme = (K8S_ROOT / "lab" / "README.md").read_text()
    assert "Not for production" in lab_readme or "不是生产" in lab_readme
    tke = (K8S_ROOT / "overlays" / "tke" / "kustomization.yaml").read_text()
    ack = (K8S_ROOT / "overlays" / "ack" / "kustomization.yaml").read_text()
    assert "YOUR_NAMESPACE" in tke and "REPLACE_TAG" in tke
    assert "YOUR_NAMESPACE" in ack and "REPLACE_TAG" in ack
