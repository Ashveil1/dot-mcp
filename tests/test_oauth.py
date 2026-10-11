"""OAuth endpoint regressions. No real accounts, tunnels, or credentials."""
import base64
import hashlib

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from dot_mcp import server as srv


@pytest.fixture
def oauth(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "OAUTH_FILE", tmp_path / "oauth.json")
    srv._oauth_ensure()
    app = Starlette(routes=[
        Route("/register", srv._oauth_register_req, methods=["POST"]),
        Route("/authorize", srv._oauth_authorize_req, methods=["GET", "POST"]),
        Route("/token", srv._oauth_token_req, methods=["POST"]),
    ])
    with TestClient(app, follow_redirects=False) as client:
        yield client


def register(client):
    response = client.post("/register", json={"redirect_uris": ["https://client.test/cb"]})
    assert response.status_code == 201
    return response.json()["client_id"]


def consent(client, cid, **overrides):
    verifier = "v" * 43
    fields = {
        "client_id": cid, "redirect_uri": "https://client.test/cb",
        "code_challenge": srv._b64u(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256", "owner_secret": srv.SECRET,
        "scope": srv.OAUTH_SCOPE,
    }
    fields.update(overrides)
    return client.post("/authorize", data=fields)


def exchange(client, cid, **overrides):
    from urllib.parse import parse_qs, urlsplit
    approved = consent(client, cid)
    assert approved.status_code == 302
    code = parse_qs(urlsplit(approved.headers["location"]).query)["code"][0]
    fields = {
        "grant_type": "authorization_code", "code": code,
        "client_id": cid, "redirect_uri": "https://client.test/cb",
        "code_verifier": "v" * 43,
    }
    fields.update(overrides)
    return client.post("/token", data=fields), fields


def test_code_exchange_and_refresh_rotation(oauth):
    cid = register(oauth)
    response, fields = exchange(oauth, cid)
    assert response.status_code == 200
    tokens = response.json()
    assert srv._jwt_verify(tokens["access_token"])["client_id"] == cid
    assert oauth.post("/token", data=fields).status_code == 400  # one-use code
    refresh = {"grant_type": "refresh_token", "client_id": cid,
               "refresh_token": tokens["refresh_token"]}
    rotated = oauth.post("/token", data=refresh)
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != tokens["refresh_token"]
    assert oauth.post("/token", data=refresh).status_code == 400


@pytest.mark.parametrize("uri", [
    "http://localhost.evil.test/cb", "http://127.0.0.1.evil.test/cb",
    "http://localhost@evil.test/cb", "https://", "https:///cb",
    "https://client.test/cb#fragment", "https://user:pass@client.test/cb",
    "https://client.test:bad/cb",
])
def test_redirect_rejects_malformed_and_loopback_lookalikes(oauth, uri):
    assert not srv._valid_redirect(uri)
    assert oauth.post("/register", json={"redirect_uris": [uri]}).status_code == 400


@pytest.mark.parametrize("uri", [
    "https://client.test/cb", "http://localhost:9876/cb",
    "http://127.0.0.1:9876/cb", "http://[::1]:9876/cb",
])
def test_redirect_accepts_https_and_exact_loopback(uri):
    assert srv._valid_redirect(uri)


@pytest.mark.parametrize("fields", [
    {"code_challenge": ""}, {"code_challenge": "short"},
    {"code_challenge_method": "plain"},
])
def test_direct_consent_post_requires_s256(oauth, fields):
    cid = register(oauth)
    response = consent(oauth, cid, **fields)
    assert response.status_code == 400
    assert not srv._oauth_state()["codes"]


def test_consent_get_preserves_challenge_method(oauth):
    cid = register(oauth)
    response = oauth.get("/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "https://client.test/cb", "code_challenge": "x" * 43,
        "code_challenge_method": "S256",
    })
    assert response.status_code == 200
    assert 'name="code_challenge_method" value="S256"' in response.text


def test_exchange_is_bound_to_client(oauth):
    cid, other = register(oauth), register(oauth)
    rejected, fields = exchange(oauth, cid, client_id=other)
    assert rejected.status_code in (400, 401)
    fields["client_id"] = cid
    assert oauth.post("/token", data=fields).status_code == 200


def test_bad_verifier_does_not_consume_code(oauth):
    cid = register(oauth)
    rejected, fields = exchange(oauth, cid, code_verifier="wrong")
    assert rejected.status_code == 400
    fields["code_verifier"] = "v" * 43
    assert oauth.post("/token", data=fields).status_code == 200


def test_wrong_client_does_not_consume_refresh(oauth):
    cid, other = register(oauth), register(oauth)
    response, _ = exchange(oauth, cid)
    fields = {"grant_type": "refresh_token", "client_id": other,
              "refresh_token": response.json()["refresh_token"]}
    assert oauth.post("/token", data=fields).status_code == 400
    fields["client_id"] = cid
    assert oauth.post("/token", data=fields).status_code == 200


def test_static_client_basic_auth(oauth):
    from urllib.parse import parse_qs, urlsplit
    static = srv._oauth_static_client()
    approved = consent(oauth, static["client_id"], code_challenge="", code_challenge_method="")
    assert approved.status_code == 302
    code = parse_qs(urlsplit(approved.headers["location"]).query)["code"][0]
    credentials = base64.b64encode(
        f'{static["client_id"]}:{static["client_secret"]}'.encode()).decode()
    response = oauth.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "https://client.test/cb",
    }, headers={"Authorization": "Basic " + credentials})
    assert response.status_code == 200
    assert srv._jwt_verify(response.json()["access_token"])


def test_wrong_owner_and_cross_origin_cannot_authorize(oauth):
    cid = register(oauth)
    assert consent(oauth, cid, owner_secret="not-owner").status_code == 403
    response = oauth.post("/authorize", data={
        "client_id": cid, "redirect_uri": "https://client.test/cb",
        "code_challenge": "x" * 43, "code_challenge_method": "S256",
        "owner_secret": srv.SECRET,
    }, headers={"Origin": "https://evil.test"})
    assert response.status_code == 403
    assert not srv._oauth_state()["codes"]


def test_static_client_post_auth(oauth):
    static = srv._oauth_static_client()
    response, _ = exchange(oauth, static["client_id"], client_secret=static["client_secret"])
    assert response.status_code == 200


def test_bad_static_secret_does_not_consume_code(oauth):
    static = srv._oauth_static_client()
    response, fields = exchange(oauth, static["client_id"], client_secret="wrong")
    assert response.status_code == 401
    fields["client_secret"] = static["client_secret"]
    assert oauth.post("/token", data=fields).status_code == 200


def test_bad_redirect_does_not_consume_code(oauth):
    cid = register(oauth)
    response, fields = exchange(oauth, cid, redirect_uri="https://elsewhere.test/cb")
    assert response.status_code == 400
    fields["redirect_uri"] = "https://client.test/cb"
    assert oauth.post("/token", data=fields).status_code == 200


@pytest.mark.parametrize("header", ["Basic %%%%", "Basic bm9jb2xvbg=="])
def test_malformed_basic_auth_returns_401(oauth, header):
    response = oauth.post("/token", data={"grant_type": "authorization_code"},
                          headers={"Authorization": header})
    assert response.status_code == 401


def test_token_response_is_not_cacheable(oauth):
    cid = register(oauth)
    response, _ = exchange(oauth, cid)
    assert response.headers.get("cache-control") == "no-store"
    assert response.headers.get("pragma") == "no-cache"


def test_metadata_advertises_supported_client_auth():
    assert set(srv._oauth_metadata()["token_endpoint_auth_methods_supported"]) == {
        "none", "client_secret_basic", "client_secret_post"}
