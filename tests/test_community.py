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
    tools.list_comments(2); tools.get_comment(3); tools.list_topics(); tools.get_topic(4); tools.list_votes(2)
    assert client.paths == [("/api/v2/community/posts/2/comments.json", None), ("/api/v2/community/comments/3.json", None), ("/api/v2/community/topics.json", None), ("/api/v2/community/topics/4.json", None), ("/api/v2/community/posts/2/votes.json", None)]


def test_subscription_and_content_tag_reads_use_official_endpoints():
    client = StubClient(); tools = CommunityTools(client)
    tools.list_post_subscriptions(2); tools.list_topic_subscriptions(4); tools.search_content_tags("bill"); tools.count_content_tags(); tools.get_content_tag("tag-1")
    assert client.paths == [("/api/v2/community/posts/2/subscriptions.json", None), ("/api/v2/community/topics/4/subscriptions.json", None), ("/api/v2/guide/content_tags.json", {"prefix": "bill"}), ("/api/v2/guide/content_tags/count.json", None), ("/api/v2/guide/content_tags/tag-1.json", None)]


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
