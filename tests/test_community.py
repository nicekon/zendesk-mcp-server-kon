from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.approvals import ApprovalStore
from zendesk_mcp_server.config import Settings
from zendesk_mcp_server.tools.community import CommunityTools


class StubClient:
    def __init__(self): self.paths = []
    def get(self, path, *, params=None):
        self.paths.append((path, params)); return success({"posts": [{"id": 2}]})

    def request(self, method, path, *, json_body=None):
        self.paths.append((method, path, json_body))
        if path == "/api/v2/guide/user_images/uploads": return success({"upload": {"url": "https://cdn.example.test/upload", "headers": {"Content-Type": "image/png"}, "token": "upload-token"}})
        if path == "/api/v2/gather/badges/icon_uploads": return success({"badge_icon_upload": {"url": "https://cdn.example.test/badge-icon", "headers": {"Content-Type": "image/png"}, "id": "badge-upload-id"}})
        return success({"post": {"id": 2}})

    def upload_presigned(self, url, headers, content):
        self.paths.append(("PUT", url, headers, content)); return success({})


def test_community_post_reads_use_fixed_endpoints():
    client = StubClient(); tools = CommunityTools(client)
    tools.list_posts(); tools.search_posts("billing"); tools.get_post(2)
    assert client.paths == [("/api/v2/community/posts.json", {"page[size]": "100"}), ("/api/v2/community/posts/search.json", {"query": "billing"}), ("/api/v2/community/posts/2.json", None)]


def test_community_comment_topic_and_vote_reads_use_fixed_endpoints():
    client = StubClient(); tools = CommunityTools(client)
    tools.list_comments(2); tools.get_comment(3); tools.list_topics(); tools.get_topic(4); tools.list_votes(2); tools.get_vote(5)
    assert client.paths == [("/api/v2/community/posts/2/comments.json", {"page[size]": "100"}), ("/api/v2/community/comments/3.json", None), ("/api/v2/community/topics.json", {"page[size]": "100"}), ("/api/v2/community/topics/4.json", None), ("/api/v2/help_center/posts/2/votes.json", {"page[size]": "100"}), ("/api/v2/help_center/votes/5.json", None)]


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
    assert result["truncated"] is False
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


def test_community_cursor_lists_normalize_posts_comments_and_topics():
    class CursorClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params))
            key = "posts" if path.endswith("/posts.json") else "comments" if path.endswith("/comments.json") else "topics"
            return success({key: [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}})

    client = CursorClient(); tools = CommunityTools(client)
    posts = tools.list_posts(topic_id=4, cursor="before", limit=2)
    comments = tools.list_comments(4, cursor="before", limit=2)
    topics = tools.list_topics(cursor="before", limit=2)

    assert posts == {"ok": True, "items": [{"id": 1}], "has_more": True, "next_cursor": "next", "truncated": False}
    assert comments == posts == topics
    assert client.paths == [
        ("/api/v2/community/topics/4/posts.json", {"page[size]": "2", "page[after]": "before"}),
        ("/api/v2/community/posts/4/comments.json", {"page[size]": "2", "page[after]": "before"}),
        ("/api/v2/community/topics.json", {"page[size]": "2", "page[after]": "before"}),
    ]


def test_subscription_and_content_tag_reads_use_official_endpoints():
    client = StubClient(); tools = CommunityTools(client)
    tools.list_post_subscriptions(2); tools.list_topic_subscriptions(4); tools.search_content_tags("bill"); tools.count_content_tags(); tools.get_content_tag("tag-1")
    assert client.paths == [("/api/v2/community/posts/2/subscriptions.json", None), ("/api/v2/community/topics/4/subscriptions.json", None), ("/api/v2/guide/content_tags.json", {"filter[name_prefix]": "bill", "page[size]": "100"}), ("/api/v2/guide/content_tags/count.json", None), ("/api/v2/guide/content_tags/tag-1.json", None)]


def test_content_tag_search_uses_cursor_envelope():
    class TagClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params)); return success({"records": [{"id": "tag-1"}], "meta": {"has_more": True, "after_cursor": "next"}})

    client = TagClient()
    result = CommunityTools(client).search_content_tags("bill", cursor="before", limit=2)

    assert result == {"ok": True, "items": [{"id": "tag-1"}], "has_more": True, "next_cursor": "next", "truncated": False}
    assert client.paths == [("/api/v2/guide/content_tags.json", {"filter[name_prefix]": "bill", "page[size]": "2", "page[after]": "before"})]


def test_community_post_create_requires_local_public_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.create_post(4, "Title", "Body")
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


def test_community_notification_previews_report_api_follower_counts(tmp_path):
    class NotificationClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params))
            key = "topic" if "/topics/" in path else "post"
            return success({key: {"follower_count": 7 if key == "topic" else 3}})

    client = NotificationClient()
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}), ApprovalStore(tmp_path / "approvals.json"))

    post_preview = tools.create_post(4, "Title", "Body", notify_subscribers=True)
    comment_preview = tools.create_comment(2, "Body", notify_subscribers=True)

    assert post_preview["data"]["follower_count"] == 7
    assert "recipient_count_unknown" not in post_preview["data"]
    assert comment_preview["data"]["follower_count"] == 3
    assert "recipient_count_unknown" not in comment_preview["data"]
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
    assert result["data"]["post"]["id"] == 2


def test_community_html_writes_reject_unsafe_tags_and_image_sources(tmp_path):
    tools = CommunityTools(StubClient(), Settings.load({"ZENDESK_SUBDOMAIN": "acme", "ZENDESK_EMAIL": "agent@example.test", "ZENDESK_API_TOKEN": "token"}), ApprovalStore(tmp_path / "approvals.json"))

    assert tools.create_post(4, "Title", "<script>alert(1)</script>")["error"]["code"] == "validation_error"
    assert tools.update_comment(2, 3, {"body": '<img src="https://evil.example/image.png">'})["error"]["code"] == "validation_error"
    assert tools.create_comment(2, '<p><a href="https://example.test">safe</a></p>')["ok"] is True


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
    assert client.paths[-2:] == [("POST", "/api/v2/guide/content_tags.json", {"content_tag": {"name": "feature request"}}), ("PUT", "/api/v2/guide/content_tags/tag-1.json", {"content_tag": {"name": "need help"}})]


def test_content_tag_delete_requires_local_destructive_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.delete_content_tag("tag-1")
    token = store.approve(preview["data"]["approval_request_id"])
    tools.delete_content_tag("tag-1", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("DELETE", "/api/v2/guide/content_tags/tag-1.json", None)


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
    result = CommunityTools(client).list_user_subscriptions("me", "followings", cursor="before", limit=2)

    assert result == {"ok": True, "items": [{"id": 5}], "has_more": True, "next_cursor": "next", "truncated": False}
    assert client.paths == [("/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings", "page[size]": "2", "page[after]": "before"})]


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
    assert client.calls == [
        ("GET", "/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings", "page[size]": "100"}),
        ("GET", "/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings", "page[size]": "100"}),
        ("POST", "/api/v2/help_center/users/me/user_subscriptions.json", {"user_subscription": {"followed_id": 7, "include_comments": True}}),
        ("GET", "/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings", "page[size]": "100"}),
    ]


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
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_IMPERSONATION": "true"}), store)
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
    assert client.paths[-3:] == [("/api/v2/gather/badge_categories.json", {"brand_id": "4"}), ("/api/v2/gather/badge_categories/category-1.json", None), ("POST", "/api/v2/gather/badge_categories.json", {"badge_category": {"brand_id": 4, "name": "Achievements", "slug": "achievements"}})]


def test_badge_category_delete_requires_local_destructive_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.delete_badge_category("category-1")
    token = store.approve(preview["data"]["approval_request_id"])
    tools.delete_badge_category("category-1", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("DELETE", "/api/v2/gather/badge_categories/category-1.json", None)


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
    assert client.paths[-5:] == [
        ("/api/v2/gather/badges.json", {"brand_id": "4"}),
        ("/api/v2/gather/badges/badge-1.json", None),
        ("POST", "/api/v2/gather/badges.json", {"badge": {"badge_category_id": "category-1", "name": "Helper", "description": "Helpful answers", "icon_upload_id": "badge-upload-id"}}),
        ("PUT", "/api/v2/gather/badges/badge-1.json", {"badge": {"name": "Super Helper"}}),
        ("DELETE", "/api/v2/gather/badges/badge-1.json", None),
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
        ("/api/v2/gather/badge_assignments.json", {"user_id": "7", "badge_id": "badge-1"}),
        ("POST", "/api/v2/gather/badge_assignments.json", {"badge_assignment": {"badge_id": "badge-1", "user_id": "7"}}),
        ("DELETE", "/api/v2/gather/badge_assignments/assignment-1.json", None),
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
        ("PUT", "https://cdn.example.test/upload", {"Content-Type": "image/png"}, b"image"),
        ("POST", "/api/v2/guide/user_images", {"token": "upload-token", "brand_id": "4"}),
    ]


def test_user_image_upload_rejects_paths_outside_the_configured_root(tmp_path):
    root = tmp_path / "uploads"; root.mkdir(); outside = tmp_path / "image.png"; outside.write_bytes(b"image")
    tools = CommunityTools(StubClient(), Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_UPLOAD_ROOT": str(root)}), ApprovalStore(tmp_path / "approvals.json"))

    assert tools.upload_user_image(str(outside), "image/png", 4)["error"]["code"] == "validation_error"


def test_badge_icon_upload_uses_the_secure_external_upload_flow(tmp_path):
    root = tmp_path / "uploads"; root.mkdir(); image = root / "icon.png"; image.write_bytes(b"image")
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_EXTERNAL_UPLOADS": "true", "ZENDESK_UPLOAD_ROOT": str(root)}), store)
    preview = tools.upload_badge_icon("icon.png", "image/png")
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.upload_badge_icon("icon.png", "image/png", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert result["data"]["badge_icon_upload_id"] == "badge-upload-id"
    assert client.paths[-2:] == [
        ("POST", "/api/v2/gather/badges/icon_uploads", {"content_type": "image/png", "file_size": 5}),
        ("PUT", "https://cdn.example.test/badge-icon", {"Content-Type": "image/png"}, b"image"),
    ]
