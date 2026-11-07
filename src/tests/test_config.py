"""Configuration failures should be clear before accepting requests."""

from pathlib import Path

import pytest

from src import config


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("YES", True), ("1", True), ("on", True),
    ("false", False), ("NO", False), ("0", False), ("off", False),
])
def test_boolean_settings_accept_documented_spellings(monkeypatch, value, expected):
    monkeypatch.setenv("WIKIWARE_TEST_BOOLEAN", value)
    assert config._parse_bool_env("WIKIWARE_TEST_BOOLEAN", False) is expected


@pytest.mark.parametrize("value", ["", "maybe", "2"])
def test_invalid_boolean_settings_fail(monkeypatch, value):
    monkeypatch.setenv("WIKIWARE_TEST_BOOLEAN", value)
    with pytest.raises(ValueError):
        config._parse_bool_env("WIKIWARE_TEST_BOOLEAN", False)


@pytest.fixture
def runtime_config(monkeypatch):
    values = {
        "DEV": True, "S3_ENDPOINT": "", "S3_ACCESS_KEY": "", "S3_SECRET_KEY": "",
        "S3_BUCKET": "wikiware", "DATABASE_URL": "postgresql+asyncpg://localhost/wikiware_test",
        "PORT": 8000, "CSRF_SECRET_KEY": "", "CSRF_COOKIE_SECURE": False,
        "SESSION_COOKIE_SECURE": False,
    }
    for key, value in values.items():
        monkeypatch.setattr(config, key, value)


def test_local_configuration_requires_no_private_module_or_storage_keys(runtime_config):
    config.validate_runtime_config()
    assert Path(config.TEMPLATE_DIR).is_absolute()
    assert Path(config.STATIC_DIR).is_absolute()


def test_incomplete_storage_configuration_fails(runtime_config, monkeypatch):
    monkeypatch.setattr(config, "S3_ENDPOINT", "https://storage.example.test")
    with pytest.raises(ValueError, match="together"):
        config.validate_runtime_config()


def test_production_requires_stable_csrf_secret(runtime_config, monkeypatch):
    monkeypatch.setattr(config, "DEV", False)
    with pytest.raises(ValueError, match="CSRF_SECRET_KEY"):
        config.validate_runtime_config()


def test_production_requires_secure_cookies(runtime_config, monkeypatch):
    monkeypatch.setattr(config, "DEV", False)
    monkeypatch.setattr(config, "CSRF_SECRET_KEY", "test-only-secret-" * 3)
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        config.validate_runtime_config()


def test_complete_production_configuration_is_accepted(runtime_config, monkeypatch):
    monkeypatch.setattr(config, "DEV", False)
    monkeypatch.setattr(config, "CSRF_SECRET_KEY", "test-only-secret-" * 3)
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "CSRF_COOKIE_SECURE", True)
    config.validate_runtime_config()


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_port_fails(runtime_config, monkeypatch, port):
    monkeypatch.setattr(config, "PORT", port)
    with pytest.raises(ValueError, match="PORT"):
        config.validate_runtime_config()
