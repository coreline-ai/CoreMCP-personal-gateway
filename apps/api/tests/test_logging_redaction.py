from __future__ import annotations

import pytest

from coremcp.db.repository import Repository
from coremcp.logging import REDACTED, redact_sensitive_data


def test_logging_redacts_sensitive_fields_and_token_like_values() -> None:
    redacted = redact_sensitive_data(
        None,
        "info",
        {
            "authorization": "Bearer cmcp_admin_secret",
            "nested": {"api_key": "key-123", "refresh_token": "rt-123"},
            "neutral": "using cmcp_client_live123 and sk-abcdefghi",
            "github": "clone token ghp_1234567890abcdef",
            "jwt": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature",
            "items": ["cmcp_refresh_refresh123", "cmcp_otk_once123", "cmcp_code_auth123"],
            "admin_token_masked": "cmcp••••ABCD",
            "client_token_masked": "cmcp_client_realvalue123",
            "safe": "visible sk short ghp_short eyJnot-a-jwt",
        },
    )

    assert redacted["authorization"] == REDACTED
    assert redacted["nested"]["api_key"] == REDACTED
    assert redacted["nested"]["refresh_token"] == REDACTED
    assert redacted["neutral"] == f"using {REDACTED} and {REDACTED}"
    assert redacted["github"] == f"clone token {REDACTED}"
    assert redacted["jwt"] == f"Bearer {REDACTED}"
    assert redacted["items"] == [REDACTED, REDACTED, REDACTED]
    assert redacted["admin_token_masked"] == "cmcp••••ABCD"
    assert redacted["client_token_masked"] == REDACTED
    assert redacted["safe"] == "visible sk short ghp_short eyJnot-a-jwt"


@pytest.mark.asyncio
async def test_audit_metadata_redacts_token_like_values_before_storage(tmp_path) -> None:
    repository = Repository(tmp_path / "audit-redaction.sqlite3")
    await repository.connect()
    try:
        audit_id = await repository.log_audit(
            action="auth.failure",
            resource_type="personal_access_token",
            resource_id="cmcp_client_resource_id_is_structural",
            request_id="req-visible",
            metadata={
                "reason": "bad token cmcp_admin_leaked123",
                "subject": "normal user input",
                "nested": {"neutral": "ghp_1234567890abcdef"},
                "credential": "sk-abcdefghi",
            },
        )
        [audit] = await repository.recent_audit_logs(limit=1, action="auth.failure")
    finally:
        await repository.close()

    assert audit["id"] == audit_id
    assert audit["request_id"] == "req-visible"
    assert audit["resource_id"] == "cmcp_client_resource_id_is_structural"
    assert audit["metadata"] == {
        "reason": f"bad token {REDACTED}",
        "subject": "normal user input",
        "nested": {"neutral": REDACTED},
        "credential": REDACTED,
    }
