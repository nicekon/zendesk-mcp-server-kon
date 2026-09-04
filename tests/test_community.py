from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.approvals import ApprovalStore
from zendesk_mcp_server.config import Settings
from zendesk_mcp_server.tools.community import CommunityTools


class StubClient:
    def __init__(self): self.paths = []
    def get(self, path, *, params=None):
        self.paths.append((path, params)); return success({"posts": [{"id": 2}]})

    def request(self, method, path, *, json_body=None):
        self.paths.append((method, path, json_body)); return success({"post": {"id": 2}})


def test_community_post_reads_use_fixed_endpoints():
    client = StubClient(); tools = CommunityTools(client)
    tools.list_posts(); tools.search_posts("billing"); tools.get_post(2)
    assert client.paths == [("/api/v2/community/posts.json", None), ("/api/v2/community/posts/search.json", {"query": "billing"}), ("/api/v2/community/posts/2.json", None)]


def test_community_comment_topic_and_vote_reads_use_fixed_endpoints():
    client = StubClient(); tools = CommunityTools(client)
    tools.list_comments(2); tools.get_comment(3); tools.list_topics(); tools.get_topic(4); tools.list_votes(2); tools.get_vote(5)
    assert client.paths == [("/api/v2/community/posts/2/comments.json", None), ("/api/v2/community/comments/3.json", None), ("/api/v2/community/topics.json", None), ("/api/v2/community/topics/4.json", None), ("/api/v2/help_center/posts/2/votes.json", None), ("/api/v2/help_center/votes/5.json", None)]


def test_subscription_and_content_tag_reads_use_official_endpoints():
    client = StubClient(); tools = CommunityTools(client)
    tools.list_post_subscriptions(2); tools.list_topic_subscriptions(4); tools.search_content_tags("bill"); tools.count_content_tags(); tools.get_content_tag("tag-1")
    assert client.paths == [("/api/v2/community/posts/2/subscriptions.json", None), ("/api/v2/community/topics/4/subscriptions.json", None), ("/api/v2/guide/content_tags.json", {"filter[name_prefix]": "bill"}), ("/api/v2/guide/content_tags/count.json", None), ("/api/v2/guide/content_tags/tag-1.json", None)]


def test_community_post_create_requires_local_public_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.create_post(4, "Title", "Body")
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.create_post(4, "Title", "Body", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert result["data"]["post"]["id"] == 2


def test_community_comment_create_requires_local_public_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.create_comment(2, "Body")
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.create_comment(2, "Body", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert result["data"]["post"]["id"] == 2


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
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    tools.list_user_subscriptions("me", "followings")
    preview = tools.upsert_user_subscription("me", 7, include_comments=False)
    token = store.approve(preview["data"]["approval_request_id"])
    tools.upsert_user_subscription("me", 7, include_comments=False, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-2:] == [("/api/v2/help_center/users/me/user_subscriptions.json", {"type": "followings"}), ("POST", "/api/v2/help_center/users/me/user_subscriptions.json", {"user_subscription": {"followed_id": 7, "include_comments": False}})]


def test_user_subscription_delete_requires_local_destructive_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.delete_user_subscription("me", 5)
    token = store.approve(preview["data"]["approval_request_id"])
    tools.delete_user_subscription("me", 5, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert client.paths[-1] == ("DELETE", "/api/v2/help_center/users/me/user_subscriptions/5.json", None)


def test_other_user_subscription_requires_public_and_impersonation_gates(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = CommunityTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_IMPERSONATION": "true"}), store)
    preview = tools.upsert_user_subscription(6, 7)
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.upsert_user_subscription(6, 7, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
    assert result["error"]["code"] == "write_disabled"
    assert client.paths == []


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
    preview = public_tools.create_badge("category-1", "Helper", "Helpful answers")
    token = store.approve(preview["data"]["approval_request_id"])
    public_tools.create_badge("category-1", "Helper", "Helpful answers", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)
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
        ("POST", "/api/v2/gather/badges.json", {"badge": {"badge_category_id": "category-1", "name": "Helper", "description": "Helpful answers"}}),
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
