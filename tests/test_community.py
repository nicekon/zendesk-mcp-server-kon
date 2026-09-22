import pytest

from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.approvals import ApprovalStore
from zendesk_mcp_server.config import Settings
from zendesk_mcp_server.tools.community import CommunityTools


@pytest.mark.parametrize("method,args,key", [("list_posts", (), "posts"), ("search_posts", ("query",), "results"), ("get_post", (2,), "post"), ("list_comments", (2,), "comments"), ("get_comment", (3,), "comment"), ("list_topics", (), "topics"), ("get_topic", (2,), "topic")])
def test_community_content_preserves_html_and_separates_untrusted_text(method, args, key):
    html = '<p>A &amp; B<img src="https://evil.test/x" alt="Screenshot"></p><p>Next</p>'
    record = {"id": 2, "title": "literal <title>", "body": html, "details": html}
    class Client:
        def get(self, path, *, params=None):
            return success({key: record if key in {"post", "comment", "topic"} else [record], "meta": {"has_more": False}, "next_page": None})
    result = getattr(CommunityTools(Client()), method)(*args)
    item = result["items"][0] if "items" in result else result["data"][key]
    assert item["untrusted_user_content"] is True
    assert item["body"] == item["details"] == html
    assert item["plain_text"] == {"title": "literal <title>", "body": "A & B[image: Screenshot]\nNext", "details": "A & B[image: Screenshot]\nNext"}
    assert "plain_text" not in record and "untrusted_user_content" not in record


def test_community_malformed_html_preserves_raw_without_claiming_plain_text():
    class Client:
        def get(self, path, *, params=None): return success({"post": {"id": 2, "details": "<![invalid]>"}})
    item = CommunityTools(Client()).get_post(2)["data"]["post"]
    assert item["details"] == "<![invalid]>" and item["untrusted_user_content"] is True
    assert item["plain_text"] == {} and item["plain_text_unavailable"] == ["details"]


class StubClient:
    def __init__(self): self.paths = []
    def get(self, path, *, params=None):
        self.paths.append((path, params))
        if path == "/api/v2/community/posts/2.json": return success({"post": {"id": 2, "details": "Body"}})
        if path == "/api/v2/community/posts/2/comments/3.json": return success({"comment": {"id": 3, "body": "Body"}})
        if path == "/api/v2/gather/badge_assignments": return success({"badge_assignments": []})
        return success({"posts": [{"id": 2}]})

    def request(self, method, path, *, json_body=None):
        self.paths.append((method, path, json_body))
        if path == "/api/v2/community/posts/2/comments.json": return success({"comment": {"id": 3, "body": "Body"}})
        if path == "/api/v2/guide/user_images/uploads": return success({"upload": {"url": "https://cdn.example.test/upload", "headers": {"Content-Type": "image/png"}, "token": "upload-token"}})
        if path == "/api/v2/guide/user_images": return success({"user_image": {"path": "/hc/user_images/image.png", "content_type": "image/png", "size": 5}})
        if path == "/api/v2/gather/badges/icon_uploads": return success({"badge_icon_upload": {"url": "https://cdn.example.test/badge-icon", "headers": {"Content-Type": "image/png"}, "id": "badge-upload-id"}})
        return success({"post": {"id": 2}})

    def upload_presigned(self, url, headers, content):
        self.paths.append(("PUT", url, headers, content if isinstance(content, bytes) else b"".join(content))); return success({})


@pytest.mark.parametrize("scope,endpoint", [({}, "/api/v2/community/posts.json"), ({"topic_id": 4}, "/api/v2/community/topics/4/posts.json"), ({"user_id": "me"}, "/api/v2/community/users/me/posts.json")])
def test_community_posts_collect_multiple_pages_with_filters(scope, endpoint):
    calls = []
    class Client:
        def get(self, path, *, params=None):
            assert path == endpoint
            assert params["filter_by"] == "completed"
            assert params["sort_by"] == "votes"
            calls.append(params)
            if params.get("page[after]") == "next":
                assert params["page[size]"] == "1"
                return success({"posts": [{"id": 101}], "meta": {"has_more": False}})
            assert params["page[size]"] == "100"
            return success({"posts": [{"id": i} for i in range(1, 101)], "meta": {"has_more": True, "after_cursor": "next"}})
    result = CommunityTools(Client()).list_posts(**scope, status="completed", sort_by="votes", limit=101)
    assert result["ok"] is True
    assert [item["id"] for item in result["items"]] == list(range(1, 102))
    assert result["has_more"] is False and result["next_cursor"] is None
    assert len(calls) == 2


@pytest.mark.parametrize("method,scope,endpoint,key", [("list_comments", {"post_id": 4}, "/api/v2/community/posts/4/comments.json", "comments"), ("list_comments", {"user_id": "me"}, "/api/v2/community/users/me/comments.json", "comments"), ("list_topics", {}, "/api/v2/community/topics.json", "topics")])
def test_community_comments_and_topics_collect_and_resume(method, scope, endpoint, key):
    class Client:
        def get(self, path, *, params=None):
            assert path == endpoint
            start = int(params.get("page[after]", "0"))
            size = int(params["page[size]"])
            assert 1 <= size <= 100
            end = min(start + size, 102)
            return success({key: [{"id": i} for i in range(start, end)], "meta": {"has_more": end < 102, "after_cursor": str(end)}})
    call = getattr(CommunityTools(Client()), method)
    first = call(**scope, limit=101)
    assert first["ok"] is True
    assert [item["id"] for item in first["items"]] == list(range(101))
    assert first["truncated"] is True and first["next_cursor"] == "101"
    last = call(**scope, cursor=first["next_cursor"])
    assert last == {"ok": True, "items": [{"id": 101}], "has_more": False, "next_cursor": None, "truncated": False}


def test_community_search_resumes_offset_and_stops_at_1000_results():
    calls = []
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/help_center/community_posts/search.json"
            assert params["query"] == "billing" and params["per_page"] == "25"
            page = int(params["page"]); calls.append(page)
            assert page <= 40
            return success({"results": [{"id": i} for i in range((page - 1) * 25, page * 25)], "next_page": "https://untrusted.example/next"})
    tools = CommunityTools(Client())
    first = tools.search_posts("billing", limit=26)
    assert [item["id"] for item in first["items"]] == list(range(26))
    assert first["next_cursor"] == "26" and first["truncated"] is True
    last = tools.search_posts("billing", cursor=first["next_cursor"], limit=1000)
    assert [item["id"] for item in last["items"]] == list(range(26, 1000))
    assert last["truncated"] is True and last["next_cursor"] is None
    assert calls == [1, 2] + list(range(2, 41))


def test_content_tag_search_collects_pages_preserving_prefix():
    calls = []
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/guide/content_tags"
            assert params["filter[name_prefix]"] == "bill"
            calls.append(params)
            index = len(calls) - 1
            assert params["page[size]"] == ("30", "30", "30", "11")[index]
            assert params.get("page[after]") == (None, "a", "b", "c")[index]
            start, end = ((0, 30), (30, 60), (60, 90), (90, 101))[index]
            return success({"records": [{"id": str(i)} for i in range(start, end)], "meta": {"has_more": index < 3, "after_cursor": ("a", "b", "c", None)[index]}})
    result = CommunityTools(Client()).search_content_tags("bill", limit=101)
    assert result["ok"] is True and len(result["items"]) == 101
    assert result["items"] == [{"id": str(i)} for i in range(101)]
    assert result["has_more"] is False and result["truncated"] is False
    assert len(calls) == 4


@pytest.mark.parametrize("direction", ["followers", "followings"])
def test_user_subscriptions_collect_pages_without_changing_direction(direction):
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/help_center/users/me/user_subscriptions.json"
            assert params["type"] == direction
            start = int(params.get("page[after]", "0")); size = int(params["page[size]"])
            assert 1 <= size <= 100
            end = min(start + size, 102)
            return success({"user_subscriptions": [{"id": i} for i in range(start, end)], "meta": {"has_more": end < 102, "after_cursor": str(end)}})
    tools = CommunityTools(Client())
    first = tools.list_user_subscriptions("me", direction, limit=101)
    assert first["ok"] is True
    assert [item["id"] for item in first["items"]] == list(range(101))
    assert first["truncated"] is True
    last = tools.list_user_subscriptions("me", direction, cursor=first["next_cursor"])
    assert last == {"ok": True, "items": [{"id": 101}], "has_more": False, "next_cursor": None, "truncated": False}


def test_badge_lists_use_documented_suffixless_routes():
    class Client:
        def get(self, path, *, params=None):
            if path.endswith(".json"):
                return {"ok": False, "error": {"code": "not_found"}}
            assert path in {"/api/v2/gather/badge_categories", "/api/v2/gather/badges", "/api/v2/gather/badge_assignments"}
            return success({path.rsplit("/", 1)[-1]: []})
    tools = CommunityTools(Client())
    for call in (tools.list_badge_categories, tools.list_badges, tools.list_badge_assignments):
        assert call()["ok"] is True


@pytest.mark.parametrize("method,key", [("list_badge_categories", "badge_categories"), ("list_badges", "badges"), ("list_badge_assignments", "badge_assignments")])
def test_badge_lists_bound_results_and_resume_local_offsets(method, key):
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/gather/" + key
            assert params == {"brand_id": "4"}
            return success({key: [{"id": str(i)} for i in range(1002)]})
    call = getattr(CommunityTools(Client()), method)
    first = call(brand_id=4)
    assert len(first["items"]) == 100 and first["next_cursor"] == "100"
    assert first["truncated"] is True
    last = call(brand_id=4, limit=1000, cursor=first["next_cursor"])
    assert [item["id"] for item in last["items"]] == [str(i) for i in range(100, 1002)]
    assert last["has_more"] is False and last["next_cursor"] is None
    assert call(brand_id=4, limit=1001)["error"]["code"] == "validation_error"


@pytest.mark.parametrize("method,segment", [("list_post_subscriptions", "posts"), ("list_topic_subscriptions", "topics")])
def test_content_subscriptions_collect_and_resume(method, segment):
    class Client:
        def get(self, path, *, params=None):
            assert path == f"/api/v2/community/{segment}/4/subscriptions.json"
            start = int(params.get("page[after]", "0")); size = int(params["page[size]"])
            assert 1 <= size <= 100
            end = min(start + size, 102)
            return success({"subscriptions": [{"id": i} for i in range(start, end)], "meta": {"has_more": end < 102, "after_cursor": str(end)}})
    call = getattr(CommunityTools(Client()), method)
    first = call(4, limit=101)
    assert [item["id"] for item in first["items"]] == list(range(101))
    assert first["truncated"] is True
    assert call(4, cursor=first["next_cursor"]) == {"ok": True, "items": [{"id": 101}], "has_more": False, "next_cursor": None, "truncated": False}


def test_user_votes_yield_resume_cursor_on_empty_nonterminal_page():
    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append(params)
            if params.get("page[after]") is None:
                return success({"votes": [], "meta": {"has_more": True, "after_cursor": "next"}})
            return success({"votes": [{"id": 7, "item_type": "Post"}], "meta": {"has_more": False}})
    tools = CommunityTools(Client())
    first = tools.list_votes(user_id="me")
    assert first["items"] == [] and first["truncated"] is True
    assert first["has_more"] is True and first["scanned_count"] == 0
    assert len(calls) == 1
    last = tools.list_votes(user_id="me", cursor=first["next_cursor"])
    assert last["items"] == [{"id": 7, "item_type": "Post"}]
    assert last["has_more"] is False


@pytest.mark.parametrize("votes,meta", [([None], {"has_more": False}), ([{"id": 1, "item_type": "Post"}] * 101, {"has_more": False}), ([], {"has_more": "false"}), ([], {})])
def test_user_votes_reject_malformed_pages(votes, meta):
    class Client:
        def get(self, path, *, params=None): return success({"votes": votes, "meta": meta})
    result = CommunityTools(Client()).list_votes(user_id="me")
    assert result["ok"] is False
    assert result["error"]["code"] == "upstream_error"


@pytest.mark.parametrize("scope", [{"post_id": 4}, {"user_id": "me"}])
def test_vote_lists_collect_more_than_one_hundred_results(scope):
    class Client:
        def get(self, path, *, params=None):
            start = int(params.get("page[after]", "0")); size = int(params["page[size]"])
            assert 1 <= size <= 100
            end = min(start + size, 150)
            return success({"votes": [{"id": i, "item_type": "Post"} for i in range(start, end)], "meta": {"has_more": end < 150, "after_cursor": str(end)}})
    result = CommunityTools(Client()).list_votes(**scope, limit=150)
    assert result["ok"] is True
    assert [item["id"] for item in result["items"]] == list(range(150))
    assert result["has_more"] is False and result["truncated"] is False


def test_user_vote_scan_cap_holds_after_a_short_page():
    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append(params)
            count = 50 if len(calls) == 1 else int(params["page[size]"])
            return success({"votes": [{"id": i, "item_type": "Article"} for i in range(count)], "meta": {"has_more": True, "after_cursor": str(len(calls))}})
    result = CommunityTools(Client()).list_votes(user_id="me", limit=1000)
    assert result["ok"] is True and result["scanned_count"] == 1000
    assert result["items"] == [] and result["truncated"] is True
    assert calls[-1]["page[size]"] == "50"
    assert len(calls) == 11


@pytest.mark.parametrize("count", [1, 2])
def test_user_votes_reject_repeated_resume_boundary_before_returning_items(count):
    from zendesk_mcp_server.tools.community import _encode_vote_cursor
    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append(params)
            return success({"votes": [{"id": i, "item_type": "Post"} for i in range(count)], "meta": {"has_more": True, "after_cursor": "same"}})
    result = CommunityTools(Client()).list_votes(user_id="me", cursor=_encode_vote_cursor("me", "same", 0), limit=1)
    assert result["ok"] is False
    assert result["error"]["code"] == "upstream_error"
    assert len(calls) == 1


@pytest.mark.parametrize("count", [1, 2])
def test_user_vote_result_limit_marks_remaining_results_truncated(count):
    class Client:
        def get(self, path, *, params=None):
            return success({"votes": [{"id": i, "item_type": "Post"} for i in range(count)], "meta": {"has_more": True, "after_cursor": "next"}})
    result = CommunityTools(Client()).list_votes(user_id="me", limit=1)
    assert result["ok"] is True and result["has_more"] is True
    assert result["truncated"] is True and result["next_cursor"]


@pytest.mark.parametrize("page", [
    {"user_subscriptions": [], "meta": {}},
    {"user_subscriptions": [None], "meta": {"has_more": False}},
    {"user_subscriptions": [], "meta": {"has_more": True, "after_cursor": "next"}},
])
def test_subscription_lookup_rejects_incomplete_pages_without_continuing(page):
    class Client:
        def __init__(self): self.calls = 0
        def get(self, path, *, params=None):
            self.calls += 1
            assert self.calls == 1
            return success(page)
    client = Client()
    result = CommunityTools(client).upsert_user_subscription("me", 7)
    assert result["ok"] is False
    assert result["error"]["code"] == "upstream_error"
    assert result["error"]["operation_state"] == "not_applied"


def test_subscription_lookup_never_scans_more_than_1000_records():
    class Client:
        def __init__(self): self.scanned = 0; self.calls = 0
        def get(self, path, *, params=None):
            self.calls += 1
            count = 50 if self.calls == 1 else int(params["page[size]"])
            self.scanned += count
            return success({"user_subscriptions": [{"followed_id": 8} for _ in range(count)], "meta": {"has_more": True, "after_cursor": str(self.calls)}})
    client = Client()
    result = CommunityTools(client).upsert_user_subscription("me", 7)
    assert result["ok"] is False and result["error"]["code"] == "upstream_error"
    assert client.scanned == 1000


def test_comment_votes_use_the_official_endpoint_and_resume():
    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append(path)
            assert path == "/api/v2/community/posts/2/comments/3/votes"
            after = params.get("page[after]")
            return success({"votes": [{"id": 9 if after else 8}], "meta": {"has_more": not bool(after), "after_cursor": "next" if not after else None}})
    tools = CommunityTools(Client())
    first = tools.list_votes(post_id=2, comment_id=3, limit=1)
    last = tools.list_votes(post_id=2, comment_id=3, limit=1, cursor=first["next_cursor"])
    assert first["items"] == [{"id": 8}] and first["has_more"] is True
    assert last["items"] == [{"id": 9}] and last["has_more"] is False
    for scope in ({"comment_id": 3}, {"user_id": "me", "comment_id": 3}, {"post_id": 2, "comment_id": True}, {"post_id": 2, "comment_id": 0}):
        assert tools.list_votes(**scope)["error"]["code"] == "validation_error"
    assert len(calls) == 2


def test_post_votes_use_the_official_community_endpoint():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/community/posts/2/votes.json"
            assert params == {"page[size]": "1"}
            return success({"votes": [], "meta": {"has_more": False}})
    assert CommunityTools(Client()).list_votes(post_id=2, limit=1) == {
        "ok": True, "items": [], "has_more": False, "next_cursor": None, "truncated": False,
    }


def test_community_post_reads_use_fixed_endpoints():
    client = StubClient(); tools = CommunityTools(client)
    tools.list_posts(); tools.search_posts("billing"); tools.get_post(2)
    assert client.paths == [("/api/v2/community/posts.json", {"page[size]": "100"}), ("/api/v2/help_center/community_posts/search.json", {"query": "billing", "per_page": "25", "page": "1"}), ("/api/v2/community/posts/2.json", None)]


def test_community_comment_topic_and_vote_reads_use_fixed_endpoints():
    client = StubClient(); tools = CommunityTools(client)
    tools.list_comments(2); tools.get_comment(3); tools.list_topics(); tools.get_topic(4); tools.list_votes(2); tools.get_vote(5)
    assert client.paths == [("/api/v2/community/posts/2/comments.json", {"page[size]": "100"}), ("/api/v2/community/comments/3.json", None), ("/api/v2/community/topics.json", {"page[size]": "100"}), ("/api/v2/community/topics/4.json", None), ("/api/v2/community/posts/2/votes.json", {"page[size]": "100"}), ("/api/v2/help_center/votes/5.json", None)]


def test_community_comment_read_uses_post_and_locale_specific_endpoint():
    client = StubClient(); tools = CommunityTools(client)

    tools.get_comment(3, post_id=2, locale="en-us")

    assert client.paths == [("/api/v2/help_center/en-us/community/posts/2/comments/3.json", None)]


def test_community_user_votes_filter_mixed_pages_until_the_requested_limit():
    class VoteClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params))
            if params.get("page[after]") == "next":
                return success({"votes": [{"id": 3, "item_type": "PostComment"}], "meta": {"has_more": False}})
            return success({"votes": [{"id": 1, "item_type": "Article"}, {"id": 2, "item_type": "Post"}], "meta": {"has_more": True, "after_cursor": "next"}})

    client = VoteClient()
    result = CommunityTools(client).list_votes(user_id="me", limit=2)

    assert result == {"ok": True, "items": [{"id": 2, "item_type": "Post"}, {"id": 3, "item_type": "PostComment"}], "has_more": False, "next_cursor": None, "truncated": False, "scanned_count": 3}
    assert client.paths == [
        ("/api/v2/help_center/users/me/votes.json", {"page[size]": "100"}),
        ("/api/v2/help_center/users/me/votes.json", {"page[size]": "100", "page[after]": "next"}),
    ]


def test_community_user_votes_return_a_resume_cursor_at_the_raw_scan_cap():
    class VoteClient:
        def __init__(self): self.calls = 0
        def get(self, path, *, params=None):
            self.calls += 1
            return success({"votes": [{"id": self.calls * 100 + offset, "item_type": "Article"} for offset in range(100)], "meta": {"has_more": True, "after_cursor": str(self.calls)}})

    client = VoteClient()
    result = CommunityTools(client).list_votes(user_id=7)

    assert result["items"] == []
    assert result["has_more"] is True
    assert result["truncated"] is True
    assert isinstance(result["next_cursor"], str)
    assert result["scanned_count"] == 1000
    assert client.calls == 10


def test_community_user_vote_cursor_resumes_an_unconsumed_mixed_page():
    class VoteClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params)); return success({"votes": [{"id": 1, "item_type": "Post"}, {"id": 2, "item_type": "PostComment"}], "meta": {"has_more": False}})

    client = VoteClient(); tools = CommunityTools(client)
    first = tools.list_votes(user_id="me", limit=1)
    second = tools.list_votes(user_id="me", limit=1, cursor=first["next_cursor"])

    assert first["items"] == [{"id": 1, "item_type": "Post"}]
    assert first["truncated"] is True
    assert second == {"ok": True, "items": [{"id": 2, "item_type": "PostComment"}], "has_more": False, "next_cursor": None, "truncated": False, "scanned_count": 2}
    assert client.paths == [
        ("/api/v2/help_center/users/me/votes.json", {"page[size]": "100"}),
        ("/api/v2/help_center/users/me/votes.json", {"page[size]": "100"}),
    ]


def test_community_user_votes_stop_when_a_page_exactly_fills_the_limit():
    class VoteClient:
        def __init__(self): self.calls = 0
        def get(self, path, *, params=None):
            self.calls += 1; return success({"votes": [{"id": 1, "item_type": "Post"}], "meta": {"has_more": True, "after_cursor": "next"}})

    client = VoteClient()
    result = CommunityTools(client).list_votes(user_id="me", limit=1)

    assert result["items"] == [{"id": 1, "item_type": "Post"}]
    assert result["has_more"] is True
    assert result["truncated"] is True
    assert client.calls == 1


def test_community_post_and_comment_lists_support_their_official_scopes_and_filters():
    client = StubClient(); tools = CommunityTools(client)

    tools.list_posts(topic_id=4, status="completed", sort_by="votes")
    tools.list_posts(user_id="me")
    tools.list_comments(user_id="me")

    assert client.paths == [
        ("/api/v2/community/topics/4/posts.json", {"filter_by": "completed", "sort_by": "votes", "page[size]": "100"}),
        ("/api/v2/community/users/me/posts.json", {"page[size]": "100"}),
        ("/api/v2/community/users/me/comments.json", {"page[size]": "100"}),
    ]


def test_community_cursor_rejects_missing_empty_and_repeated_continuations():
    for cursor in (None, "", "before", 42):
        class InvalidCursorClient:
            def get(self, path, *, params=None):
                return success({"topics": [], "meta": {"has_more": True, "after_cursor": cursor}})

        result = CommunityTools(InvalidCursorClient()).list_topics(cursor="before")
        assert result["ok"] is False, cursor
        assert result["error"]["code"] == "upstream_error"


def test_community_empty_final_page_clears_continuation():
    class FinalPageClient:
        def get(self, path, *, params=None):
            return success({"topics": [], "meta": {"has_more": False, "after_cursor": "before"}})

    result = CommunityTools(FinalPageClient()).list_topics(cursor="before")
    assert result == {"ok": True, "items": [], "has_more": False, "next_cursor": None, "truncated": False}


def test_community_cursor_lists_normalize_posts_comments_and_topics():
    class CursorClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params))
            key = "posts" if path.endswith("/posts.json") else "comments" if path.endswith("/comments.json") else "topics"
            return success({key: [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}})

    client = CursorClient(); tools = CommunityTools(client)
    posts = tools.list_posts(topic_id=4, cursor="before", limit=1)
    comments = tools.list_comments(4, cursor="before", limit=1)
    topics = tools.list_topics(cursor="before", limit=1)

    assert posts == {"ok": True, "items": [{"id": 1}], "has_more": True, "next_cursor": "next", "truncated": True}
    assert comments == topics == posts
    assert client.paths == [
        ("/api/v2/community/topics/4/posts.json", {"page[size]": "1", "page[after]": "before"}),
        ("/api/v2/community/posts/4/comments.json", {"page[size]": "1", "page[after]": "before"}),
        ("/api/v2/community/topics.json", {"page[size]": "1", "page[after]": "before"}),
    ]


def test_subscription_and_content_tag_reads_use_official_endpoints():
    client = StubClient(); tools = CommunityTools(client)
    tools.list_post_subscriptions(2); tools.list_topic_subscriptions(4); tools.search_content_tags("bill"); tools.count_content_tags(); tools.get_content_tag("tag-1")
    assert client.paths == [("/api/v2/community/posts/2/subscriptions.json", {"page[size]": "100"}), ("/api/v2/community/topics/4/subscriptions.json", {"page[size]": "100"}), ("/api/v2/guide/content_tags", {"filter[name_prefix]": "bill", "page[size]": "30"}), ("/api/v2/guide/content_tags/count", None), ("/api/v2/guide/content_tags/tag-1", None)]


def test_opaque_ids_reject_path_rewriting_before_requests_or_approvals(tmp_path):
    client = StubClient()
    approval_path = tmp_path / "approvals.json"
    tools = CommunityTools(client, approvals=ApprovalStore(approval_path))
    methods = (tools.get_content_tag, tools.delete_content_tag, tools.get_badge,
               tools.delete_badge, tools.get_badge_category, tools.delete_badge_category,
               tools.delete_badge_assignment)
    for identifier in (".", "..", "%2e%2e", "%252f", "a\\b", "a\nb", "a\rb", "a\tb", "a\x00b", "a\x7fb"):
        for method in methods:
            result = method(identifier)
            assert result["ok"] is False
            assert result["error"]["code"] == "validation_error"
    assert client.paths == []
    assert not approval_path.exists()


def test_post_update_preserves_string_and_integer_content_tag_ids(tmp_path):
    client = StubClient()
    store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    payload = {"content_tag_ids": ["01GFXGBX7YZ9ASWTCVMASTK8ZS", 6776]}
    preview = tools.update_post(2, payload)
    assert preview["ok"] is True
    assert client.paths == []
    token = store.approve(preview["data"]["approval_request_id"])
    tools.update_post(2, payload, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths == [("PUT", "/api/v2/community/posts/2.json", {"post": payload})]
    for invalid in (True, 0, -1, "", " ", None, {}, "a/b"):
        assert tools.update_post(2, {"content_tag_ids": [invalid]})["ok"] is False


def test_content_tag_count_validates_response_and_preserves_upstream_errors():
    class Client:
        response = None
        def get(self, path, *, params=None):
            assert path == "/api/v2/guide/content_tags/count"
            return self.response

    client = Client()
    tools = CommunityTools(client)
    for data in ({}, None, [], {"value": True}, {"value": False}, {"value": -1}, {"value": "6"}, {"value": 1.5}):
        client.response = {"ok": True, "data": data}
        result = tools.count_content_tags()
        assert result["ok"] is False
        assert result["error"]["code"] == "upstream_error"
    for value in (0, 6):
        client.response = success({"value": value, "refreshed_at": "2022-10-13T12:00:00.000Z"})
        assert tools.count_content_tags() == client.response
    client.response = {"ok": False, "error": {"code": "permission_denied"}}
    assert tools.count_content_tags() == client.response


def test_content_tag_search_uses_cursor_envelope():
    class TagClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params)); return success({"records": [{"id": "tag-1"}], "meta": {"has_more": True, "after_cursor": "next"}})

    client = TagClient()
    result = CommunityTools(client).search_content_tags("bill", cursor="before", limit=1)

    assert result == {"ok": True, "items": [{"id": "tag-1"}], "has_more": True, "next_cursor": "next", "truncated": True}
    assert client.paths == [("/api/v2/guide/content_tags", {"filter[name_prefix]": "bill", "page[size]": "1", "page[after]": "before"})]


def test_community_post_create_requires_local_public_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({}), store)
    preview = tools.create_post(4, "Title", "Body")
    assert tools.create_post(4, "Title", "Body", execution_mode="apply")["error"]["code"] == "approval_required"
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.create_post(4, "Title", "Body", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert result["data"]["post"]["id"] == 2


def test_community_post_author_or_created_at_requires_impersonation_gate(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.create_post(4, "Title", "Body", author_id=9, created_at="2026-09-01T00:00:00Z", notify_subscribers=True)
    token = store.approve(preview["data"]["approval_request_id"])
    blocked = tools.create_post(4, "Title", "Body", author_id=9, created_at="2026-09-01T00:00:00Z", notify_subscribers=True, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert preview["data"]["impersonation"] is True
    assert preview["data"]["recipient_count_unknown"] is True
    assert blocked["error"]["code"] == "write_disabled"
    assert client.paths == [("/api/v2/community/topics/4.json", None)]


@pytest.mark.parametrize("method,args", [("create_post", (4, "Title", "Body")), ("create_comment", (2, "Body"))])
@pytest.mark.parametrize("delegation", [{"author_id": 9}, {"created_at": "2026-09-01T00:00:00Z"}])
@pytest.mark.parametrize("role", ["admin", "agent", "end-user", None])
def test_community_content_impersonation_checks_current_admin_before_write(tmp_path, method, args, delegation, role):
    class Client(StubClient):
        def get(self, path, *, params=None):
            if path == "/api/v2/users/me.json":
                self.paths.append((path, params))
                return success({"user": {"id": 1, "role": role}})
            return super().get(path, params=params)
    client = Client(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true", "ZENDESK_ENABLE_IMPERSONATION": "true"}), store)
    call = getattr(tools, method)
    preview = call(*args, **delegation)
    assert client.paths == []
    token = store.approve(preview["data"]["approval_request_id"])
    result = call(*args, **delegation, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[0] == ("/api/v2/users/me.json", None)
    if role == "admin":
        assert result["ok"] is True
        assert sum(path[0] == "POST" for path in client.paths) == 1
    else:
        assert result["error"]["code"] == "permission_denied"
        assert len(client.paths) == 1


@pytest.mark.parametrize("followers", [7, 0, None, True, -1, "7"])
def test_community_notification_previews_report_api_follower_counts(tmp_path, followers):
    class NotificationClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params))
            key = "topic" if "/topics/" in path else "post"
            return success({key: {"follower_count": followers}})

    client = NotificationClient()
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}), ApprovalStore(tmp_path / "approvals.json"))

    post_preview = tools.create_post(4, "Title", "Body", notify_subscribers=True)
    comment_preview = tools.create_comment(2, "Body", notify_subscribers=True)

    assert post_preview["data"]["topic_id"] == 4
    assert comment_preview["data"]["post_id"] == 2
    for preview in (post_preview, comment_preview):
        assert preview["data"]["outbound_write"] is False
        if type(followers) is int and followers >= 0:
            assert preview["data"]["follower_count"] == followers
            assert "recipient_count_unknown" not in preview["data"]
        else:
            assert preview["data"]["recipient_count_unknown"] is True
            assert "follower_count" not in preview["data"]
    assert client.paths == [
        ("/api/v2/community/topics/4.json", None),
        ("/api/v2/community/posts/2.json", None),
    ]


def test_community_comment_create_requires_local_public_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.create_comment(2, "Body")
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.create_comment(2, "Body", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert result["data"]["comment"]["id"] == 3


@pytest.mark.parametrize("operation,key,field,args", [
    ("create_post", "post", "details", (4, "Title", "<b>Body</b>")),
    ("update_post", "post", "details", (2, {"details": "<b>Body</b>"})),
    ("create_comment", "comment", "body", (2, "<b>Body</b>")),
    ("update_comment", "comment", "body", (2, 3, {"body": "<b>Body</b>"})),
])
@pytest.mark.parametrize("observed", ["normalized", "error", "wrong_id", "invalid_body", "invalid_created_id"])
def test_html_write_reads_back_without_replaying(tmp_path, operation, key, field, args, observed):
    from zendesk_mcp_server.contracts import ErrorCode, failure
    calls = []
    identifier = 2 if key == "post" else 3
    class Client:
        def request(self, method, path, *, json_body=None):
            calls.append(method)
            return success({key: {"id": None if observed == "invalid_created_id" else identifier, field: "stale", "url": "https://evil.test/"}}, request_id="write-1", operation_state="applied")
        def get(self, path, *, params=None):
            calls.append("GET")
            assert path == ("/api/v2/community/posts/2.json" if key == "post" else "/api/v2/community/posts/2/comments/3.json")
            if observed == "error": return failure(ErrorCode.PERMISSION_DENIED, "denied")
            return success({key: {"id": 99 if observed == "wrong_id" else identifier, field: None if observed == "invalid_body" else "<strong>Body</strong>"}})
    store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(Client(), Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    call = getattr(tools, operation)
    preview = call(*args)
    assert calls == []
    request_id = preview["data"]["approval_request_id"]
    token = store.approve(request_id)
    result = call(*args, execution_mode="apply", approval_request_id=request_id, approval_token=token)
    if observed == "normalized" or (observed == "invalid_created_id" and operation.startswith("update")):
        assert result["data"][key][field] == "<strong>Body</strong>"
        assert result["data"][key]["plain_text"][field] == "Body"
        assert result["data"][key]["untrusted_user_content"] is True
        assert result["operation_state"] == "applied" and result["request_id"] == "write-1"
    else:
        assert result["error"]["code"] == "partial_success"
        assert result["error"]["operation_state"] == "applied" and result["error"]["retryable"] is False
        if observed != "invalid_created_id": assert result["error"]["details"]["resource_id"] == identifier
    assert len([method for method in calls if method != "GET"]) == 1
    assert calls.count("GET") == (0 if observed == "invalid_created_id" and operation.startswith("create") else 1)
    assert call(*args, execution_mode="apply", approval_request_id=request_id, approval_token=token)["error"]["code"] == "approval_required"


def test_failed_html_write_does_not_read_back_or_replay(tmp_path):
    from zendesk_mcp_server.contracts import ErrorCode, failure
    calls = []
    expected = failure(ErrorCode.OUTCOME_UNKNOWN, "write outcome unknown", operation_state="unknown")
    class Client:
        def request(self, *args, **kwargs):
            calls.append("write")
            return expected
        def get(self, *args, **kwargs): raise AssertionError("failed write must not read back")
    store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(Client(), Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.create_post(4, "Title", "Body")
    request_id = preview["data"]["approval_request_id"]
    token = store.approve(request_id)
    assert tools.create_post(4, "Title", "Body", execution_mode="apply", approval_request_id=request_id, approval_token=token) == expected
    assert calls == ["write"]


@pytest.mark.parametrize("method,args", [
    ("create_post", (4, "Title", "<![invalid]>")),
    ("update_post", (2, {"details": "<![invalid]>"})),
    ("create_comment", (2, "<![invalid]>")),
    ("update_comment", (2, 3, {"body": "<![invalid]>"})),
])
def test_community_html_writes_reject_unknown_declarations_before_approval(method, args):
    class Client:
        def get(self, *args, **kwargs): raise AssertionError("invalid HTML must not read")
        def request(self, *args, **kwargs): raise AssertionError("invalid HTML must not write")
    tools = CommunityTools(Client())
    for mode in ("preview", "apply"):
        assert getattr(tools, method)(*args, execution_mode=mode)["error"]["code"] == "validation_error"


@pytest.mark.parametrize("html", [
    '<img src="/hc/user_images/../../articles/123">',
    '<img src="https://acme.zendesk.com/hc/user_images/../../articles/123">',
    '<img src="/hc/user_images/%2e%2e%2farticles">',
    '<img src="/hc/user_images/%252e%252e%252farticles">',
    '<img src="/hc/user_images/..">',
    '<img src="/hc/user_images/">',
    '<x-zendesk-user>1<b>extra</b></x-zendesk-user>',
    '<x-zendesk-user>1<br></x-zendesk-user>',
    '<x-zendesk-user>1<x-zendesk-user>2</x-zendesk-user></x-zendesk-user>',
])
def test_community_html_rejects_image_path_escape_and_nested_mentions_before_io(html):
    class Client:
        def get(self, *args, **kwargs): raise AssertionError("Invalid HTML must not read")
        def request(self, *args, **kwargs): raise AssertionError("Invalid HTML must not write")
    tools = CommunityTools(Client(), Settings.load({"ZENDESK_SUBDOMAIN": "acme"}))
    for mode in ("preview", "apply"):
        for call in (
            lambda: tools.create_post(4, "Title", html, execution_mode=mode),
            lambda: tools.create_comment(2, html, execution_mode=mode),
            lambda: tools.update_post(2, {"details": html}, execution_mode=mode),
            lambda: tools.update_comment(2, 3, {"body": html}, execution_mode=mode),
        ):
            assert call()["error"]["code"] == "validation_error"


def test_community_html_writes_reject_unsafe_tags_and_image_sources(tmp_path):
    tools = CommunityTools(StubClient(), Settings.load({"ZENDESK_SUBDOMAIN": "acme", "ZENDESK_EMAIL": "agent@example.test", "ZENDESK_API_TOKEN": "token"}), ApprovalStore(tmp_path / "approvals.json"))

    assert tools.create_post(4, "Title", "<script>alert(1)</script>")["error"]["code"] == "validation_error"
    assert tools.update_comment(2, 3, {"body": '<img src="https://evil.example/image.png">'})["error"]["code"] == "validation_error"
    assert tools.create_comment(2, '<p><a href="https://example.test">safe</a></p>')["ok"] is True
    for body in ('<img src="/hc/user_images/image.png">', '<img src="https://acme.zendesk.com/hc/user_images/image.png">', '<x-zendesk-user>123</x-zendesk-user>'):
        assert tools.create_comment(2, body)["ok"] is True


def test_community_topic_create_requires_local_public_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.create_topic("Ideas", "Product ideas")
    token = store.approve(preview["data"]["approval_request_id"])
    assert tools.create_topic("Ideas", "Product ideas", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)["data"]["post"]["id"] == 2


def test_community_post_update_requires_local_public_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.update_post(2, {"title": "Updated"})
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.update_post(2, {"title": "Updated"}, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert result["data"]["post"]["id"] == 2
    assert client.paths[-1] == ("PUT", "/api/v2/community/posts/2.json", {"post": {"title": "Updated"}})


def test_community_post_delete_requires_local_destructive_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.delete_post(2)
    token = store.approve(preview["data"]["approval_request_id"])
    tools.delete_post(2, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("DELETE", "/api/v2/community/posts/2.json", None)


def test_community_comment_update_requires_local_public_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.update_comment(2, 3, {"official": True})
    token = store.approve(preview["data"]["approval_request_id"])
    tools.update_comment(2, 3, {"official": True}, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("PUT", "/api/v2/community/posts/2/comments/3.json", {"comment": {"official": True}})


def test_community_comment_delete_requires_local_destructive_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.delete_comment(2, 3)
    token = store.approve(preview["data"]["approval_request_id"])
    tools.delete_comment(2, 3, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("DELETE", "/api/v2/community/posts/2/comments/3.json", None)


def test_community_topic_update_requires_local_public_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.update_topic(4, {"manageable_by": "staff"})
    token = store.approve(preview["data"]["approval_request_id"])
    tools.update_topic(4, {"manageable_by": "staff"}, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("PUT", "/api/v2/community/topics/4.json", {"topic": {"manageable_by": "staff"}})


def test_community_topic_delete_requires_local_destructive_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.delete_topic(4)
    token = store.approve(preview["data"]["approval_request_id"])
    tools.delete_topic(4, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("DELETE", "/api/v2/community/topics/4.json", None)


def test_community_vote_requires_local_public_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.cast_vote("post_comment", 2, 3, "down")
    token = store.approve(preview["data"]["approval_request_id"])
    tools.cast_vote("post_comment", 2, 3, "down", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("POST", "/api/v2/community/posts/2/comments/3/down.json", None)


def test_community_vote_delete_requires_local_destructive_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.delete_vote(5)
    token = store.approve(preview["data"]["approval_request_id"])
    tools.delete_vote(5, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("DELETE", "/api/v2/help_center/votes/5.json", None)


def test_community_subscription_get_and_create_require_correct_paths_and_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    tools.get_subscription("post", 2, 5)
    preview = tools.create_subscription("topic", 4, include_comments=True)
    token = store.approve(preview["data"]["approval_request_id"])
    tools.create_subscription("topic", 4, include_comments=True, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-2:] == [("/api/v2/community/posts/2/subscriptions/5.json", None), ("POST", "/api/v2/community/topics/4/subscriptions.json", {"subscription": {"include_comments": True}})]


def test_community_topic_subscription_update_and_delete_require_approvals(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    update_tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = update_tools.update_subscription(4, 5, include_comments=True)
    token = store.approve(preview["data"]["approval_request_id"])
    update_tools.update_subscription(4, 5, include_comments=True, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    delete_tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = delete_tools.delete_subscription("topic", 4, 5)
    token = store.approve(preview["data"]["approval_request_id"])
    delete_tools.delete_subscription("topic", 4, 5, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-2:] == [("PUT", "/api/v2/community/topics/4/subscriptions/5.json", {"subscription": {"include_comments": True}}), ("DELETE", "/api/v2/community/topics/4/subscriptions/5.json", None)]


def test_content_tag_create_and_update_require_local_public_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    create = tools.create_content_tag("feature request")
    token = store.approve(create["data"]["approval_request_id"])
    tools.create_content_tag("feature request", execution_mode="apply", approval_request_id=create["data"]["approval_request_id"], approval_token=token)
    update = tools.update_content_tag("tag-1", "need help")
    token = store.approve(update["data"]["approval_request_id"])
    tools.update_content_tag("tag-1", "need help", execution_mode="apply", approval_request_id=update["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-2:] == [("POST", "/api/v2/guide/content_tags", {"content_tag": {"name": "feature request"}}), ("PUT", "/api/v2/guide/content_tags/tag-1", {"content_tag": {"name": "need help"}})]


def test_content_tag_delete_requires_local_destructive_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.delete_content_tag("tag-1")
    token = store.approve(preview["data"]["approval_request_id"])
    tools.delete_content_tag("tag-1", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("DELETE", "/api/v2/guide/content_tags/tag-1", None)


def test_user_subscription_invalid_mode_stops_before_lookup():
    class Client:
        def get(self, *args, **kwargs): raise AssertionError("invalid mode must not read")
    result = CommunityTools(Client()).upsert_user_subscription("me", 1, execution_mode="invalid")
    assert result["error"]["code"] == "validation_error"


@pytest.mark.parametrize("followed_id", [True, None, "1", 0])
def test_user_subscription_rejects_malformed_followed_id_before_noop(followed_id):
    class Client:
        def get(self, path, *, params=None):
            return success({"user_subscriptions": [{"id": 5, "followed_id": followed_id, "include_comments": False}], "meta": {"has_more": False}})
        def request(self, *args, **kwargs): raise AssertionError("malformed response must not write")
    result = CommunityTools(Client()).upsert_user_subscription("me", 1)
    assert result["error"]["code"] == "upstream_error"


def test_user_subscription_list_and_upsert_require_fixed_path_and_public_approval(tmp_path):
    class SubscriptionClient(StubClient):
        def get(self, path, *, params=None):
            self.paths.append((path, params)); return success({"user_subscriptions": [], "meta": {"has_more": False}})

    client = SubscriptionClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    tools.list_user_subscriptions("me", "followings")
    preview = tools.upsert_user_subscription("me", 7, include_comments=False)
    token = store.approve(preview["data"]["approval_request_id"])
    tools.upsert_user_subscription("me", 7, include_comments=False, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-4:] == [
        ("/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings", "page[size]": "100"}),
        ("/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings", "page[size]": "100"}),
        ("POST", "/api/v2/help_center/users/me/user_subscriptions.json", {"user_subscription": {"followed_id": 7, "include_comments": False}}),
        ("/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings", "page[size]": "100"}),
    ]


def test_user_subscription_list_uses_cursor_envelope():
    class SubscriptionClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params)); return success({"user_subscriptions": [{"id": 5}], "meta": {"has_more": True, "after_cursor": "next"}})

    client = SubscriptionClient()
    result = CommunityTools(client).list_user_subscriptions("me", "followings", cursor="before", limit=1)

    assert result == {"ok": True, "items": [{"id": 5}], "has_more": True, "next_cursor": "next", "truncated": True}
    assert client.paths == [("/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings", "page[size]": "1", "page[after]": "before"})]


def test_user_subscription_upsert_is_idempotent_when_the_setting_already_matches(tmp_path):
    class SubscriptionClient:
        def __init__(self): self.get_calls, self.write_calls = [], []
        def get(self, path, *, params=None):
            self.get_calls.append((path, params)); return success({"user_subscriptions": [{"id": 5, "followed_id": 7, "include_comments": False}], "meta": {"has_more": False}})
        def request(self, method, path, *, json_body=None): self.write_calls.append((method, path, json_body)); return success({})

    client = SubscriptionClient()
    result = CommunityTools(client, Settings.load({}), ApprovalStore(tmp_path / "approvals.json")).upsert_user_subscription("me", 7, include_comments=False, execution_mode="apply")

    assert result["data"] == {"current_subscription": {"id": 5, "followed_id": 7, "include_comments": False}, "subscription": {"id": 5, "followed_id": 7, "include_comments": False}, "operation_state": "not_applied", "idempotent": True, "outbound_write": False}
    assert client.write_calls == []


@pytest.mark.parametrize("value,requested", [(0, False), (1, True)])
def test_subscription_numeric_setting_is_not_verified_as_idempotent(tmp_path, value, requested):
    class Client:
        def get(self, path, *, params=None):
            return success({"user_subscriptions": [{"id": 5, "followed_id": 7, "include_comments": value}], "meta": {"has_more": False}})
        def request(self, *args, **kwargs):
            raise AssertionError("Preview must not write")
    tools = CommunityTools(Client(), Settings.load({}), ApprovalStore(tmp_path / "approvals.json"))
    result = tools.upsert_user_subscription("me", 7, include_comments=requested)
    assert result["ok"] is True and result["data"]["execution_mode"] == "preview"
    assert result["data"]["outbound_write"] is False


def test_user_subscription_upsert_previews_change_and_reads_back_after_approval(tmp_path):
    class SubscriptionClient:
        def __init__(self): self.include_comments, self.calls = False, []
        def get(self, path, *, params=None):
            self.calls.append(("GET", path, params)); return success({"user_subscriptions": [{"id": 5, "followed_id": 7, "include_comments": self.include_comments}], "meta": {"has_more": False}})
        def request(self, method, path, *, json_body=None):
            self.calls.append((method, path, json_body)); self.include_comments = json_body["user_subscription"]["include_comments"]; return success({"user_subscription": {"id": 5}})

    client = SubscriptionClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.upsert_user_subscription("me", 7, include_comments=True)
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.upsert_user_subscription("me", 7, include_comments=True, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert preview["data"]["current_subscription"] == {"id": 5, "followed_id": 7, "include_comments": False}
    assert preview["data"]["proposed_subscription"] == {"followed_id": 7, "include_comments": True}
    assert result["data"]["subscription"] == {"id": 5, "followed_id": 7, "include_comments": True}
    assert result["data"]["operation_state"] == "applied"
    assert result["operation_state"] == "applied"
    assert client.calls == [
        ("GET", "/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings", "page[size]": "100"}),
        ("GET", "/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings", "page[size]": "100"}),
        ("POST", "/api/v2/help_center/users/me/user_subscriptions.json", {"user_subscription": {"followed_id": 7, "include_comments": True}}),
        ("GET", "/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings", "page[size]": "100"}),
    ]


@pytest.mark.parametrize("observed", [
    {"user_subscriptions": [{"id": 5, "followed_id": 7, "include_comments": 1}], "meta": {"has_more": False}},
    {"user_subscriptions": [{"id": 5, "followed_id": 7}], "meta": {"has_more": False}},
    {"user_subscriptions": [], "meta": {}},
    {"user_subscriptions": [], "meta": {"has_more": False}},
    {"user_subscriptions": [{"id": 5, "followed_id": 7, "include_comments": False}], "meta": {"has_more": False}},
])
def test_subscription_write_with_unverified_readback_is_unknown_without_retry(tmp_path, observed):
    class Client:
        def __init__(self): self.writes = 0; self.reads = 0
        def get(self, path, *, params=None):
            self.reads += 1
            return success(observed if self.writes else {"user_subscriptions": [], "meta": {"has_more": False}})
        def request(self, method, path, *, json_body=None):
            assert method == "POST"
            assert path == "/api/v2/help_center/users/me/user_subscriptions.json"
            assert json_body == {"user_subscription": {"followed_id": 7, "include_comments": True}}
            self.writes += 1
            return success({})
    client = Client(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.upsert_user_subscription("me", 7, include_comments=True)
    assert client.writes == 0
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.upsert_user_subscription("me", 7, include_comments=True, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert result["ok"] is False
    assert result["error"]["code"] == "outcome_unknown"
    assert result["error"]["operation_state"] == "unknown"
    assert result["error"]["retryable"] is False
    assert client.writes == 1 and client.reads == 3


def test_user_subscription_delete_requires_local_destructive_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.delete_user_subscription("me", 5)
    token = store.approve(preview["data"]["approval_request_id"])
    tools.delete_user_subscription("me", 5, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("DELETE", "/api/v2/help_center/users/me/user_subscriptions/5.json", None)


def test_other_user_subscription_requires_public_and_impersonation_gates(tmp_path):
    class SubscriptionClient(StubClient):
        def get(self, path, *, params=None):
            self.paths.append((path, params)); return success({"user_subscriptions": [], "meta": {"has_more": False}})

    client = SubscriptionClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "false", "ZENDESK_ENABLE_IMPERSONATION": "true"}), store)
    preview = tools.upsert_user_subscription(6, 7)
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.upsert_user_subscription(6, 7, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert result["error"]["code"] == "write_disabled"
    assert not any(isinstance(call[0], str) and call[0] == "POST" for call in client.paths)


def test_badge_category_reads_and_mutations_use_fixed_paths_and_approvals(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    tools.list_badge_categories(4); tools.get_badge_category("category-1")
    preview = tools.create_badge_category(4, "Achievements", "achievements")
    token = store.approve(preview["data"]["approval_request_id"])
    tools.create_badge_category(4, "Achievements", "achievements", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-3:] == [("/api/v2/gather/badge_categories", {"brand_id": "4"}), ("/api/v2/gather/badge_categories/category-1", None), ("POST", "/api/v2/gather/badge_categories", {"badge_category": {"brand_id": 4, "name": "Achievements", "slug": "achievements"}})]


def test_badge_category_delete_requires_local_destructive_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.delete_badge_category("category-1")
    token = store.approve(preview["data"]["approval_request_id"])
    tools.delete_badge_category("category-1", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("DELETE", "/api/v2/gather/badge_categories/category-1", None)


def test_badge_delete_binds_cascade_snapshot_to_approval(tmp_path):
    class Client(StubClient):
        assignments = [{"id": "a", "badge_id": "badge-1", "user_id": "7"}]
        def get(self, path, *, params=None):
            assert path == "/api/v2/gather/badge_assignments"
            assert params == {"badge_id": "badge-1"}
            return success({"badge_assignments": self.assignments})
    client = Client()
    store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.delete_badge("badge-1")
    assert preview["data"]["cascade_assignment_count"] == 1
    assert preview["data"]["irreversible"] is True
    assert store.preview(preview["data"]["approval_request_id"])["payload"]["cascade_assignment_count"] == 1
    token = store.approve(preview["data"]["approval_request_id"])
    client.assignments = [{"id": "b", "badge_id": "badge-1", "user_id": "8"}]
    result = tools.delete_badge("badge-1", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert result["error"]["code"] == "approval_required"
    assert client.paths == []
    preview = tools.delete_badge("badge-1")
    token = store.approve(preview["data"]["approval_request_id"])
    assert tools.delete_badge("badge-1", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)["ok"] is True
    assert client.paths == [("DELETE", "/api/v2/gather/badges/badge-1", None)]


def test_badge_delete_cannot_approve_unknown_cascade(tmp_path):
    class Client(StubClient):
        response = None
        def get(self, path, *, params=None): return self.response
    client = Client()
    path = tmp_path / "approvals.json"
    tools = CommunityTools(client, approvals=ApprovalStore(path))
    for assignments in (None, [None], [{"id": "a", "badge_id": "other"}], [{"id": "a", "badge_id": "badge-1"}] * 2):
        client.response = success({"badge_assignments": assignments})
        assert tools.delete_badge("badge-1")["error"]["code"] == "upstream_error"
    client.response = {"ok": False, "error": {"code": "permission_denied"}}
    assert tools.delete_badge("badge-1") == client.response
    assert client.paths == []
    assert not path.exists()


def test_badges_use_fixed_paths_and_local_approvals(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    public_tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    public_tools.list_badges(4); public_tools.get_badge("badge-1")
    preview = public_tools.create_badge("category-1", "Helper", "Helpful answers", icon_upload_id="badge-upload-id")
    token = store.approve(preview["data"]["approval_request_id"])
    public_tools.create_badge("category-1", "Helper", "Helpful answers", icon_upload_id="badge-upload-id", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    update = public_tools.update_badge("badge-1", {"name": "Super Helper"})
    token = store.approve(update["data"]["approval_request_id"])
    public_tools.update_badge("badge-1", {"name": "Super Helper"}, execution_mode="apply", approval_request_id=update["data"]["approval_request_id"], approval_token=token)
    delete_tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    delete = delete_tools.delete_badge("badge-1")
    token = store.approve(delete["data"]["approval_request_id"])
    delete_tools.delete_badge("badge-1", execution_mode="apply", approval_request_id=delete["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-7:] == [
        ("/api/v2/gather/badges", {"brand_id": "4"}),
        ("/api/v2/gather/badges/badge-1", None),
        ("POST", "/api/v2/gather/badges", {"badge": {"badge_category_id": "category-1", "name": "Helper", "description": "Helpful answers", "icon_upload_id": "badge-upload-id"}}),
        ("PUT", "/api/v2/gather/badges/badge-1", {"badge": {"name": "Super Helper"}}),
        ("/api/v2/gather/badge_assignments", {"badge_id": "badge-1"}),
        ("/api/v2/gather/badge_assignments", {"badge_id": "badge-1"}),
        ("DELETE", "/api/v2/gather/badges/badge-1", None),
    ]


def test_badge_assignments_require_public_impersonation_and_destructive_gates(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    blocked_tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = blocked_tools.create_badge_assignment("badge-1", 7)
    token = store.approve(preview["data"]["approval_request_id"])
    blocked = blocked_tools.create_badge_assignment("badge-1", 7, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert blocked["error"]["code"] == "write_disabled"
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true", "ZENDESK_ENABLE_IMPERSONATION": "true"}), store)
    tools.list_badge_assignments(user_id=7, badge_id="badge-1")
    preview = tools.create_badge_assignment("badge-1", 7)
    token = store.approve(preview["data"]["approval_request_id"])
    tools.create_badge_assignment("badge-1", 7, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    delete_tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true", "ZENDESK_ENABLE_IMPERSONATION": "true"}), store)
    preview = delete_tools.delete_badge_assignment("assignment-1")
    token = store.approve(preview["data"]["approval_request_id"])
    delete_tools.delete_badge_assignment("assignment-1", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-3:] == [
        ("/api/v2/gather/badge_assignments", {"user_id": "7", "badge_id": "badge-1"}),
        ("POST", "/api/v2/gather/badge_assignments", {"badge_assignment": {"badge_id": "badge-1", "user_id": "7"}}),
        ("DELETE", "/api/v2/gather/badge_assignments/assignment-1", None),
    ]


def test_user_image_upload_requires_approved_external_upload_and_safe_local_file(tmp_path):
    root = tmp_path / "uploads"; root.mkdir(); image = root / "image.png"; image.write_bytes(b"image")
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    disabled = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_UPLOAD_ROOT": str(root)}), store)
    preview = disabled.upload_user_image("image.png", "image/png", 4)
    token = store.approve(preview["data"]["approval_request_id"])
    result = disabled.upload_user_image("image.png", "image/png", 4, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert result["error"]["code"] == "write_disabled"
    enabled = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_EXTERNAL_UPLOADS": "true", "ZENDESK_UPLOAD_ROOT": str(root)}), store)
    preview = enabled.upload_user_image("image.png", "image/png", 4)
    token = store.approve(preview["data"]["approval_request_id"])
    enabled.upload_user_image("image.png", "image/png", 4, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-3:] == [
        ("POST", "/api/v2/guide/user_images/uploads", {"content_type": "image/png", "file_size": 5}),
        ("PUT", "https://cdn.example.test/upload", {"Content-Type": "image/png", "Content-Length": "5"}, b"image"),
        ("POST", "/api/v2/guide/user_images", {"token": "upload-token", "brand_id": "4"}),
    ]


@pytest.mark.parametrize("badge,stage,count", [(False, "prepare_upload", 1), (False, "upload_binary", 2), (False, "create_image_path", 3), (True, "prepare_upload", 1), (True, "upload_binary", 2)])
def test_image_upload_failure_identifies_stage_without_replay(tmp_path, badge, stage, count):
    from zendesk_mcp_server.contracts import failure, ErrorCode
    (tmp_path / "image.png").write_bytes(b"image")
    denied = failure(ErrorCode.PERMISSION_DENIED, "denied", operation_state="not_applied", request_id="request-1", details={"required_scopes": ["hc:write"]})
    class Client(StubClient):
        def request(self, method, path, *, json_body=None):
            result = super().request(method, path, json_body=json_body)
            current = "prepare_upload" if path.endswith(("/uploads", "/icon_uploads")) else "create_image_path"
            return denied if current == stage else result
        def upload_presigned(self, url, headers, content):
            result = super().upload_presigned(url, headers, content)
            return denied if stage == "upload_binary" else result
    client = Client(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_EXTERNAL_UPLOADS": "true", "ZENDESK_UPLOAD_ROOT": str(tmp_path)}), store)
    upload = tools.upload_badge_icon if badge else tools.upload_user_image
    args = ("image.png", "image/png") if badge else ("image.png", "image/png", 4)
    preview = upload(*args)
    request_id = preview["data"]["approval_request_id"]
    result = upload(*args, execution_mode="apply", approval_request_id=request_id, approval_token=store.approve(request_id))
    assert result["error"]["details"]["upload_stage"] == stage
    assert result["error"]["details"]["required_scopes"] == ["hc:write"]
    assert result["error"]["code"] == "permission_denied"
    assert result["error"]["operation_state"] == "not_applied"
    assert result["error"]["request_id"] == "request-1"
    assert len(client.paths) == count
    assert "upload_stage" not in denied["error"].get("details", {})


@pytest.mark.parametrize("case,stage,count", [("missing_upload", "prepare_upload", 1), ("invalid_headers", "prepare_upload", 1), ("framing", "prepare_upload", 1), ("invalid_path", "create_image_path", 3)])
def test_malformed_image_upload_response_identifies_stage(tmp_path, case, stage, count):
    (tmp_path / "image.png").write_bytes(b"image")
    class Client(StubClient):
        def request(self, method, path, *, json_body=None):
            result = super().request(method, path, json_body=json_body)
            if path.endswith("/uploads"):
                if case == "missing_upload": result = success({})
                elif case == "invalid_headers": result["data"]["upload"]["headers"] = None
                elif case == "framing": result["data"]["upload"]["headers"]["Content-Length"] = "999"
            elif case == "invalid_path": result["data"]["user_image"]["path"] = "/invalid/path"
            result["request_id"] = "request-2"
            return result
    client = Client(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_EXTERNAL_UPLOADS": "true", "ZENDESK_UPLOAD_ROOT": str(tmp_path)}), store)
    preview = tools.upload_user_image("image.png", "image/png", 4)
    request_id = preview["data"]["approval_request_id"]
    result = tools.upload_user_image("image.png", "image/png", 4, execution_mode="apply", approval_request_id=request_id, approval_token=store.approve(request_id))
    assert result["error"]["details"]["upload_stage"] == stage
    assert result["error"]["code"] == "upstream_error"
    assert result["error"]["operation_state"] == "unknown"
    assert result["error"]["request_id"] == "request-2"
    assert len(client.paths) == count


def test_user_image_upload_rejects_paths_outside_the_configured_root(tmp_path):
    root = tmp_path / "uploads"; root.mkdir(); outside = tmp_path / "image.png"; outside.write_bytes(b"image")
    tools = CommunityTools(StubClient(), Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_UPLOAD_ROOT": str(root)}), ApprovalStore(tmp_path / "approvals.json"))

    assert tools.upload_user_image(str(outside), "image/png", 4)["error"]["code"] == "validation_error"


@pytest.mark.parametrize("headers", [{"content-length": "6"}, {"Transfer-Encoding": "chunked"}])
def test_image_upload_rejects_conflicting_framing_before_put(tmp_path, headers):
    (tmp_path / "image.png").write_bytes(b"image")
    class Client(StubClient):
        def request(self, method, path, *, json_body=None):
            result = super().request(method, path, json_body=json_body)
            if "upload" in result["data"]: result["data"]["upload"]["headers"] = headers
            return result
    client = Client(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_EXTERNAL_UPLOADS": "true", "ZENDESK_UPLOAD_ROOT": str(tmp_path)}), store)
    preview = tools.upload_user_image("image.png", "image/png", 4)
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.upload_user_image("image.png", "image/png", 4, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert result["ok"] is False and result["error"]["code"] == "upstream_error"
    assert len(client.paths) == 1


def test_image_uploads_stream_the_approved_snapshot_and_close_it(tmp_path, monkeypatch):
    import hashlib
    import tempfile
    from collections.abc import Iterator
    import zendesk_mcp_server.tools.community as module

    snapshots = []
    original = tempfile.TemporaryFile
    def temporary(*args, **kwargs):
        file = original(*args, **kwargs); snapshots.append(file); return file
    monkeypatch.setattr(module, "TemporaryFile", temporary, raising=False)
    image = tmp_path / "image.png"
    expected = b"a" * 65536 + b"b" * 65536 + b"c"
    class Client(StubClient):
        def request(self, method, path, *, json_body=None):
            image.write_bytes(b"changed after approval")
            return super().request(method, path, json_body=json_body)
        def upload_presigned(self, url, headers, content):
            assert isinstance(content, Iterator)
            assert headers["Content-Length"] == "131073"
            chunks = list(content)
            assert [len(chunk) for chunk in chunks] == [65536, 65536, 1]
            assert b"".join(chunks) == expected
            return success({})
    client = Client(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_EXTERNAL_UPLOADS": "true", "ZENDESK_UPLOAD_ROOT": str(tmp_path)}), store)
    for method, args in ((tools.upload_user_image, ("image.png", "image/png", 4)), (tools.upload_badge_icon, ("image.png", "image/png"))):
        image.write_bytes(expected)
        preview = method(*args)
        assert preview["data"]["sha256"] == hashlib.sha256(expected).hexdigest()
        assert snapshots and all(file.closed for file in snapshots)
        token = store.approve(preview["data"]["approval_request_id"])
        result = method(*args, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
        assert result["ok"] is True
        assert all(file.closed for file in snapshots)
        image.write_bytes(expected)
        preview = method(*args)
        token = store.approve(preview["data"]["approval_request_id"])
        image.write_bytes(b"different before apply")
        before = list(client.paths)
        rejected = method(*args, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
        assert rejected["error"]["code"] == "approval_required"
        assert client.paths == before
        assert all(file.closed for file in snapshots)
        image.write_bytes(b"x" * 2_000_001)
        assert method(*args)["error"]["code"] == "validation_error"
        assert all(file.closed for file in snapshots)


def test_image_upload_rejects_parent_symlink_swap_before_open(tmp_path, monkeypatch):
    import os
    root = tmp_path / "uploads"; root.mkdir()
    nested = root / "nested"; nested.mkdir()
    image = nested / "image.png"; image.write_bytes(b"allowed")
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "image.png").write_bytes(b"private outside data")
    original_open = os.open
    swapped = []
    def race(path, flags, *args, **kwargs):
        if not swapped and str(path) in {str(image), "nested"}:
            nested.rename(root / "original")
            nested.symlink_to(outside, target_is_directory=True)
            swapped.append(True)
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", race)
    client = StubClient()
    tools = CommunityTools(client, Settings.load({"ZENDESK_UPLOAD_ROOT": str(root)}), ApprovalStore(tmp_path / "approvals.json"))
    result = tools.upload_user_image("nested/image.png", "image/png", 4)
    assert swapped
    assert result["error"]["code"] == "validation_error"
    assert client.paths == []


def test_image_upload_requires_secure_platform_primitives(tmp_path, monkeypatch):
    monkeypatch.setattr("zendesk_mcp_server.tools.community._SECURE_UPLOAD_OPEN", False)
    client = StubClient()
    tools = CommunityTools(client, Settings.load({"ZENDESK_UPLOAD_ROOT": str(tmp_path)}))
    for result in (tools.upload_user_image("image.png", "image/png", 4), tools.upload_badge_icon("image.png", "image/png")):
        assert result["error"]["code"] == "unsupported"
        assert result["error"]["operation_state"] == "not_applied"
    assert client.paths == []


def test_image_upload_rejects_fifo_without_blocking_and_closes_descriptors(tmp_path, monkeypatch):
    import os
    import pytest
    fifo = tmp_path / "image.png"; os.mkfifo(fifo)
    original_open = os.open
    descriptors = []
    def checked_open(path, flags, *args, **kwargs):
        if str(path) == "image.png": assert flags & os.O_NONBLOCK
        descriptor = original_open(path, flags, *args, **kwargs)
        descriptors.append(descriptor)
        return descriptor
    monkeypatch.setattr(os, "open", checked_open)
    tools = CommunityTools(StubClient(), Settings.load({"ZENDESK_UPLOAD_ROOT": str(tmp_path)}))
    assert tools.upload_user_image("image.png", "image/png", 4)["error"]["code"] == "validation_error"
    assert descriptors
    for descriptor in descriptors:
        with pytest.raises(OSError): os.fstat(descriptor)


@pytest.mark.parametrize("details", [None, {}, {"url": "https://cdn.example.test/upload", "headers": {}, "token": "", "id": ""}])
def test_image_upload_rejects_success_response_missing_upload_details(tmp_path, details):
    image = tmp_path / "image.png"; image.write_bytes(b"image")
    class Client(StubClient):
        def request(self, method, path, *, json_body=None):
            self.paths.append((method, path))
            return success({} if details is None else {"upload": details, "badge_icon_upload": details})
    for badge in (False, True):
        client = Client(); store = ApprovalStore(tmp_path / "approvals.json")
        tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_EXTERNAL_UPLOADS": "true", "ZENDESK_UPLOAD_ROOT": str(tmp_path)}), store)
        method = tools.upload_badge_icon if badge else tools.upload_user_image
        args = ("image.png", "image/png") if badge else ("image.png", "image/png", 4)
        preview = method(*args)
        request_id = preview["data"]["approval_request_id"]
        token = store.approve(request_id)
        result = method(*args, execution_mode="apply", approval_request_id=request_id, approval_token=token)
        assert result["ok"] is False
        assert result["error"]["code"] == "upstream_error"
        assert result["error"]["operation_state"] == "unknown"
        assert len(client.paths) == 1


@pytest.mark.parametrize("image", [None, {}, {"path": ""}, {"path": "https://external.example/image.png"}, {"path": "/hc/user_images/"}, {"path": "/hc/user_images/%2e%2e%2farticles"}])
def test_user_image_creation_requires_a_usable_image_path(tmp_path, image):
    (tmp_path / "image.png").write_bytes(b"image")
    class Client(StubClient):
        def request(self, method, path, *, json_body=None):
            if path == "/api/v2/guide/user_images":
                self.paths.append((method, path, json_body))
                return success({"user_image": image})
            return super().request(method, path, json_body=json_body)
    client = Client(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_EXTERNAL_UPLOADS": "true", "ZENDESK_UPLOAD_ROOT": str(tmp_path)}), store)
    preview = tools.upload_user_image("image.png", "image/png", 4)
    request_id = preview["data"]["approval_request_id"]
    result = tools.upload_user_image("image.png", "image/png", 4, execution_mode="apply", approval_request_id=request_id, approval_token=store.approve(request_id))
    assert result["ok"] is False
    assert result["error"]["code"] == "upstream_error"
    assert result["error"]["operation_state"] == "unknown"
    assert len(client.paths) == 3


def test_badge_icon_upload_uses_the_secure_external_upload_flow(tmp_path):
    root = tmp_path / "uploads"; root.mkdir(); image = root / "icon.png"; image.write_bytes(b"image")
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_EXTERNAL_UPLOADS": "true", "ZENDESK_UPLOAD_ROOT": str(root)}), store)
    preview = tools.upload_badge_icon("icon.png", "image/png")
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.upload_badge_icon("icon.png", "image/png", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert result["data"]["badge_icon_upload_id"] == "badge-upload-id"
    assert result["operation_state"] == "applied"
    assert client.paths[-2:] == [
        ("POST", "/api/v2/gather/badges/icon_uploads", {"content_type": "image/png", "file_size": 5}),
        ("PUT", "https://cdn.example.test/badge-icon", {"Content-Type": "image/png", "Content-Length": "5"}, b"image"),
    ]
