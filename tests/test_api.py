"""API tests: auth, linking profiles, and /stats. The platform calls are replaced
with fakes, so nothing here uses the network or the real database."""
from datetime import datetime, timezone

import main

TODAY = datetime.now(timezone.utc).date().isoformat()


# ---------- auth ----------

def test_signup_rejects_duplicate_email(client, auth):
    r = client.post(
        "/signup",
        json={"username": "someone_else", "email": "ALICE@example.com", "password": "password123"},
    )
    assert r.status_code == 409  # emails are normalised, so the case does not matter


def test_signup_rejects_short_password(client):
    r = client.post(
        "/signup",
        json={"username": "bob", "email": "bob@example.com", "password": "short"},
    )
    assert r.status_code == 422


def test_login_wrong_password(client, auth):
    r = client.post("/login", json={"email": "alice@example.com", "password": "wrong-password"})
    assert r.status_code == 401


def test_me_requires_a_token(client):
    assert client.get("/me").status_code in (401, 403)
    assert client.get("/me", headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401


def test_me_returns_current_user(client, auth):
    _, headers = auth
    r = client.get("/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


def test_cannot_read_another_users_data(client, auth):
    user_id, headers = auth
    assert client.get(f"/users/{user_id + 1}/profiles", headers=headers).status_code == 403


# ---------- linking profiles ----------

def link(client, user_id, headers, platform, handle):
    return client.post(
        f"/users/{user_id}/profiles",
        json={"platform": platform, "handle": handle},
        headers=headers,
    )


def test_link_profile_success_duplicate_and_list(client, auth, monkeypatch):
    user_id, headers = auth
    monkeypatch.setattr(main, "validate_handle", lambda platform, handle: None)

    r = link(client, user_id, headers, "atcoder", "tourist")
    assert r.status_code == 200
    assert r.json()["handle"] == "tourist"

    assert link(client, user_id, headers, "atcoder", "TOURIST").status_code == 409

    profiles = client.get(f"/users/{user_id}/profiles", headers=headers).json()
    assert [(p["platform"], p["handle"]) for p in profiles] == [("atcoder", "tourist")]


def test_link_profile_unknown_handle_is_404_and_not_saved(client, auth, monkeypatch):
    user_id, headers = auth
    monkeypatch.setattr(main, "validate_handle", lambda platform, handle: "not_found")

    assert link(client, user_id, headers, "github", "nobody-here").status_code == 404
    assert client.get(f"/users/{user_id}/profiles", headers=headers).json() == []


def test_link_profile_platform_down_is_503(client, auth, monkeypatch):
    user_id, headers = auth
    monkeypatch.setattr(main, "validate_handle", lambda platform, handle: "rate_limited")

    assert link(client, user_id, headers, "github", "someone").status_code == 503


def test_link_profile_unsupported_platform_is_rejected(client, auth):
    user_id, headers = auth
    assert link(client, user_id, headers, "geeksforgeeks", "someone").status_code == 422


def test_unlink_profile(client, auth, monkeypatch):
    user_id, headers = auth
    monkeypatch.setattr(main, "validate_handle", lambda platform, handle: None)
    profile_id = link(client, user_id, headers, "leetcode", "someone").json()["id"]

    r = client.delete(f"/users/{user_id}/profiles/{profile_id}", headers=headers)
    assert r.status_code == 204
    assert client.get(f"/users/{user_id}/profiles", headers=headers).json() == []


# ---------- /stats ----------

FAKE_STATS = {
    "codeforces": {
        "rating": 1000, "max_rating": 1100, "rank": "newbie", "max_rank": "newbie",
        "all": 10, "easy": 8, "medium": 2, "hard": 0,
        "topics": [{"name": "dp", "count": 3}],
        "activity": {TODAY: 2},
    },
    "leetcode": {
        "all": 20, "easy": 10, "medium": 8, "hard": 2,
        "topics": [{"name": "Dynamic Programming", "count": 4}],
        "activity": {TODAY: 1},
    },
    "atcoder": {
        "rating": 1500, "max_rating": 1600, "rank": "cyan", "max_rank": "cyan",
        "contests": 5, "all": 30, "activity": {TODAY: 3},
    },
    "github": {
        "name": "Alice", "avatar_url": None, "public_repos": 4, "followers": 1,
        "following": 2, "stars": 7, "forks": 1, "languages": [], "top_repos": [],
        "events": {TODAY: 9},
    },
}


def install_fake_fetchers(monkeypatch, counter=None):
    def fake(platform):
        def fetch(handle):
            if counter is not None:
                counter[platform] = counter.get(platform, 0) + 1
            return FAKE_STATS[platform], None
        return fetch

    monkeypatch.setattr(main, "fetch_codeforces_all", fake("codeforces"))
    monkeypatch.setattr(main, "fetch_leetcode", fake("leetcode"))
    monkeypatch.setattr(main, "fetch_atcoder", fake("atcoder"))
    monkeypatch.setattr(main, "fetch_github", fake("github"))


def link_all_four(client, user_id, headers, monkeypatch):
    monkeypatch.setattr(main, "validate_handle", lambda platform, handle: None)
    for platform in ("codeforces", "leetcode", "atcoder", "github"):
        assert link(client, user_id, headers, platform, f"{platform}_user").status_code == 200


def test_stats_summary_combines_all_platforms(client, auth, monkeypatch):
    user_id, headers = auth
    link_all_four(client, user_id, headers, monkeypatch)
    install_fake_fetchers(monkeypatch)

    body = client.get(f"/users/{user_id}/stats", headers=headers).json()
    summary = body["summary"]

    assert summary["profiles_linked"] == 4
    assert summary["profiles_resolved"] == 4
    assert summary["total_solved"] == 60            # 10 + 20 + 30; GitHub has no "all"
    assert summary["leetcode_total_solved"] == 20
    assert summary["best_codeforces_rating"] == 1000
    assert summary["best_atcoder_rating"] == 1500
    # GitHub "events" must NOT be mixed into problem-solving activity
    assert summary["submissions"] == 6              # 2 + 1 + 3
    assert summary["active_days"] == 1
    assert summary["current_streak"] == 1
    assert summary["topics"] == [{"name": "Dynamic Programming", "count": 7}]  # dp + DP merged

    assert {p["platform"] for p in body["profiles"]} == {"codeforces", "leetcode", "atcoder", "github"}
    assert all(p["error"] is None for p in body["profiles"])


def test_stats_are_cached_and_refresh_bypasses_the_cache(client, auth, monkeypatch):
    user_id, headers = auth
    link_all_four(client, user_id, headers, monkeypatch)
    calls = {}
    install_fake_fetchers(monkeypatch, counter=calls)

    client.get(f"/users/{user_id}/stats", headers=headers)
    client.get(f"/users/{user_id}/stats", headers=headers)
    assert calls == {"codeforces": 1, "leetcode": 1, "atcoder": 1, "github": 1}

    client.get(f"/users/{user_id}/stats?refresh=true", headers=headers)
    assert calls == {"codeforces": 2, "leetcode": 2, "atcoder": 2, "github": 2}


def test_stats_reports_the_reason_when_a_platform_fails(client, auth, monkeypatch):
    user_id, headers = auth
    monkeypatch.setattr(main, "validate_handle", lambda platform, handle: None)
    link(client, user_id, headers, "leetcode", "someone")
    monkeypatch.setattr(main, "fetch_leetcode", lambda handle: (None, "rate_limited"))

    body = client.get(f"/users/{user_id}/stats", headers=headers).json()

    entry = body["profiles"][0]
    assert entry["stats"] is None
    assert entry["error"] == "rate_limited"
    assert body["summary"]["profiles_linked"] == 1
    assert body["summary"]["profiles_resolved"] == 0
