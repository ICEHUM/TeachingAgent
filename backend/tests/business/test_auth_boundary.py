from __future__ import annotations

import pytest

from app.main import create_app


def test_production_refuses_dev_identity_header_mode():
    with pytest.raises(RuntimeError, match="DEV_AUTH_ENABLED"):
        create_app(
            database_url="sqlite+aiosqlite:///:memory:",
            sqlite_test_mode=True,
            environment="production",
            dev_auth_enabled=True,
        )


def test_dev_identity_header_is_explicit_and_defaults_off():
    disabled = create_app(
        database_url="sqlite+aiosqlite:///:memory:",
        sqlite_test_mode=True,
        environment="development",
        dev_auth_enabled=False,
    )
    enabled = create_app(
        database_url="sqlite+aiosqlite:///:memory:",
        sqlite_test_mode=True,
        environment="test",
        dev_auth_enabled=True,
    )
    assert disabled.state.dev_auth_enabled is False
    assert enabled.state.dev_auth_enabled is True
