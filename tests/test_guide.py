from zendesk_mcp_server.contracts import ErrorCode, failure, success
from zendesk_mcp_server.approvals import ApprovalStore
from zendesk_mcp_server.config import Settings
from zendesk_mcp_server.tools.guide import GuideTools


class StubClient:
    def __init__(self): self.paths = []
    def get(self, path, *, params=None):
        self.paths.append((path, params))
        if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us"]})
        return success({"articles": [{"id": 3}]})

    def request(self, method, path, *, json_body=None):
        self.paths.append((method, path, json_body)); return success({"article": {"id": 4}})


def test_guide_and_csat_reads_use_fixed_endpoints():
    client = StubClient()
    tools = GuideTools(client)

    tools.list_categories()
    tools.list_sections()
    tools.search_articles("billing")
    tools.get_article(3)
    tools.get_satisfaction_ratings()
    tools.list_permission_groups()
    tools.list_user_segments(built_in=False)

    assert client.paths == [
        ("/api/v2/help_center/categories.json", None),
        ("/api/v2/help_center/sections.json", None),
        ("/api/v2/help_center/articles/search.json", {"query": "billing"}),
        ("/api/v2/help_center/articles/3.json", None),
        ("/api/v2/satisfaction_ratings.json", None),
        ("/api/v2/guide/permission_groups.json", None),
        ("/api/v2/help_center/user_segments.json", {"built_in": "false"}),
    ]


def test_draft_article_create_validates_locale_and_requires_local_approval(tmp_path):
    client = StubClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = GuideTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}), store)
    preview = tools.create_article(3, "en-us", "Title", "<p>Body</p>", labels=["billing"], position=1)
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.create_article(3, "en-us", "Title", "<p>Body</p>", labels=["billing"], position=1, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert result["data"]["article"]["id"] == 4
    assert client.paths[-2:] == [
        ("/api/v2/help_center/locales.json", None),
        ("POST", "/api/v2/help_center/sections/3/articles.json", {"article": {"title": "Title", "body": "<p>Body</p>", "locale": "en-us", "draft": True, "label_names": ["billing"], "position": 1}, "notify_subscribers": False}),
    ]


def test_article_create_rejects_publishing_before_any_request(tmp_path):
    client = StubClient(); tools = GuideTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}), ApprovalStore(tmp_path / "approvals.json"))

    assert tools.create_article(3, "en-us", "Title", "Body", draft=False)["error"]["code"] == "validation_error"
    assert client.paths == []


class TranslationClient(StubClient):
    def __init__(self, existing): super().__init__(); self.existing, self.published = existing, False
    def get(self, path, *, params=None):
        self.paths.append((path, params))
        if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us"]})
        if path == "/api/v2/help_center/articles/3/translations/en-us.json": return success({"translation": {"id": 6, "locale": "en-us", "title": "Old", "draft": not self.published}}) if self.existing else failure(ErrorCode.NOT_FOUND, "missing")
        return success({})
    def request(self, method, path, *, json_body=None):
        self.paths.append((method, path, json_body))
        if json_body == {"translation": {"draft": False}}: self.published = True
        return success({"translation": {"id": 6}})


def test_translation_upsert_refuses_to_replace_an_existing_body(tmp_path):
    client = TranslationClient(existing=True)
    tools = GuideTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}), ApprovalStore(tmp_path / "approvals.json"))

    result = tools.upsert_article_translation(3, "en-us", title="New", body="<p>New body</p>")

    assert result["error"]["code"] == "article_body_replace_required"
    assert not any(path[0] == "POST" for path in client.paths)


def test_translation_upsert_creates_only_a_draft_after_approval(tmp_path):
    client = TranslationClient(existing=False); store = ApprovalStore(tmp_path / "approvals.json")
    tools = GuideTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}), store)
    preview = tools.upsert_article_translation(3, "en-us", title="New", body="<p>New body</p>")
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.upsert_article_translation(3, "en-us", title="New", body="<p>New body</p>", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert result["data"]["translation"]["id"] == 6
    assert client.paths[-3:] == [
        ("/api/v2/help_center/articles/3/translations/en-us.json", None),
        ("/api/v2/help_center/locales.json", None),
        ("POST", "/api/v2/help_center/articles/3/translations.json", {"translation": {"locale": "en-us", "title": "New", "body": "<p>New body</p>", "draft": True}}),
    ]


def test_translation_body_replacement_is_destructive_and_separate(tmp_path):
    client = TranslationClient(existing=True); store = ApprovalStore(tmp_path / "approvals.json")
    tools = GuideTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
    preview = tools.replace_article_translation_body(3, "en-us", "<p>Replacement</p>")
    token = store.approve(preview["data"]["approval_request_id"])
    tools.replace_article_translation_body(3, "en-us", "<p>Replacement</p>", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert preview["data"]["destructive"] is True
    assert client.paths[-3:] == [
        ("/api/v2/help_center/articles/3/translations/en-us.json", None),
        ("/api/v2/help_center/locales.json", None),
        ("PUT", "/api/v2/help_center/articles/3/translations/en-us.json", {"translation": {"body": "<p>Replacement</p>"}}),
    ]


def test_publish_translation_requires_public_approval_and_reads_back(tmp_path):
    client = TranslationClient(existing=True); store = ApprovalStore(tmp_path / "approvals.json")
    tools = GuideTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.publish_article(3, "en-us")
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.publish_article(3, "en-us", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert result["data"]["translation"]["draft"] is False
    assert client.paths[-4:] == [
        ("/api/v2/help_center/articles/3/translations/en-us.json", None),
        ("/api/v2/help_center/locales.json", None),
        ("PUT", "/api/v2/help_center/articles/3/translations/en-us.json", {"translation": {"draft": False}}),
        ("/api/v2/help_center/articles/3/translations/en-us.json", None),
    ]
