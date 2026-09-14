"""Homework 2, Part D: authentication tests for the session endpoints.

These run entirely offline. None of them sends a message, because a message
runs the agent and would need a model provider key; every test stops at the
authorization boundary instead. No Langfuse and no Docker are required. The
tracing itself is checked through the recorded spans in Part E.

The claim under test is narrow and worth stating plainly: this server does
not authenticate anyone (POST /sessions issues a token for whatever user id
the caller names). What it does enforce is authorization - a caller cannot
claim a role the database does not give that user, cannot choose their own
store, and cannot reuse one session's token against another session.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from server import app as server_app


@pytest.fixture(autouse=True)
def clean_sessions(world: dict[str, Path]) -> None:
    """Each test starts with an empty server-side session table."""
    server_app._SESSIONS.clear()


def _new_session(user_id: int, role: str) -> dict:
    return server_app.create_session(
        server_app.SessionCreate(user_id=user_id, role=role)
    )


# ---------------------------------------------------------------------------
# Session creation validates the claimed identity against the database.
# ---------------------------------------------------------------------------


def test_create_session_rejects_a_role_the_database_does_not_agree_with() -> None:
    """User 1 is a shopper. Claiming 'support' must be refused with 403.

    This is the required case: the role comes from the stored user row, not
    from the request, so a caller cannot promote themselves by asking.
    """
    with pytest.raises(HTTPException) as exc:
        _new_session(user_id=1, role="support")

    assert exc.value.status_code == 403
    assert not server_app._SESSIONS, "a rejected claim must not create a session"


def test_create_session_rejects_an_unknown_user() -> None:
    with pytest.raises(HTTPException) as exc:
        _new_session(user_id=999_999, role="shopper")

    assert exc.value.status_code == 404


def test_create_session_rejects_an_unknown_role() -> None:
    with pytest.raises(HTTPException) as exc:
        _new_session(user_id=1, role="administrator")

    assert exc.value.status_code == 400


def test_create_session_takes_the_store_from_the_database() -> None:
    """A merchant does not get to choose their own store.

    The request carries only user_id and role; store_id 2 can therefore only
    have come from the stored user row.
    """
    response = _new_session(user_id=9002, role="merchant")
    payload = server_app.verify_token(response["token"])

    assert payload["store_id"] == 2
    ctx, _ = server_app._SESSIONS[response["session_id"]]
    assert (ctx.user_id, ctx.role, ctx.store_id) == (9002, "merchant", 2)


# ---------------------------------------------------------------------------
# A token authorizes exactly one session, and only if it is unmodified.
# ---------------------------------------------------------------------------


def test_a_token_cannot_authorize_a_different_session() -> None:
    """The required case: session A's token must not open session B.

    Both tokens are validly signed by this server, so the signature check
    passes and the binding between token and session is what rejects it.
    """
    shopper = _new_session(user_id=1, role="shopper")
    merchant = _new_session(user_id=9002, role="merchant")

    with pytest.raises(HTTPException) as exc:
        server_app._authorize(
            merchant["session_id"], f"Bearer {shopper['token']}"
        )

    assert exc.value.status_code == 403


def test_a_session_token_authorizes_its_own_session() -> None:
    """The positive control, so the test above cannot pass for the wrong reason."""
    shopper = _new_session(user_id=1, role="shopper")

    ctx = server_app._authorize(
        shopper["session_id"], f"Bearer {shopper['token']}"
    )

    assert (ctx.user_id, ctx.role) == (1, "shopper")


def test_a_tampered_token_is_rejected() -> None:
    """Editing the payload to claim another user breaks the HMAC signature.

    Without the signature check, the token is just a base64 dictionary and
    anyone could rewrite user_id.
    """
    shopper = _new_session(user_id=1, role="shopper")
    body, signature = shopper["token"].rsplit(".", 1)

    payload = json.loads(base64.urlsafe_b64decode(body.encode()))
    payload["user_id"] = 9501
    payload["role"] = "support"
    forged_body = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True).encode()
    ).decode()

    assert server_app.verify_token(f"{forged_body}.{signature}") is None

    with pytest.raises(HTTPException) as exc:
        server_app._authorize(
            shopper["session_id"], f"Bearer {forged_body}.{signature}"
        )

    assert exc.value.status_code == 401


def test_a_missing_bearer_token_is_rejected() -> None:
    shopper = _new_session(user_id=1, role="shopper")

    with pytest.raises(HTTPException) as exc:
        server_app._authorize(shopper["session_id"], None)

    assert exc.value.status_code == 401
