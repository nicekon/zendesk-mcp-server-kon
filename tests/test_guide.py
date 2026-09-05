import os
from pathlib import Path

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


def test_guide_category_read_resolves_brand_id_to_its_subdomain():
    class BrandClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params))
            return success({"brand": {"id": 7, "subdomain": "brand-one", "has_help_center": True}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            self.paths.append((subdomain, path, params))
            return success({"categories": [{"id": "guide-1"}]})

    client = BrandClient()
    result = GuideTools(client).list_categories(brand_id=7)

    assert result["data"]["categories"] == [{"id": "guide-1"}]
    assert client.paths == [
        ("/api/v2/brands/7.json", None),
        ("brand-one", "/api/v2/help_center/categories.json", None),
    ]


def test_guide_search_uses_official_brand_and_locale_filters():
    client = StubClient()

    GuideTools(client).search_articles("billing", brand_id=7, locale="en-us")

    assert client.paths == [("/api/v2/help_center/articles/search.json", {"query": "billing", "brand_id": "7", "locale": "en-us"})]


def test_guide_article_read_resolves_brand_id_to_its_subdomain():
    class BrandClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params)); return success({"brand": {"subdomain": "brand-one", "has_help_center": True}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            self.paths.append((subdomain, path, params)); return success({"article": {"id": "guide-1"}})

    client = BrandClient()
    result = GuideTools(client).get_article("guide-1", brand_id=7)

    assert result["data"]["article"]["id"] == "guide-1"
    assert client.paths[-1] == ("brand-one", "/api/v2/help_center/articles/guide-1.json", None)


def test_article_export_uses_locale_cursor_pagination():
    class ExportClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params))
            if params and params.get("page[after]") == "next": return success({"articles": [{"id": 2}], "meta": {"has_more": False}})
            return success({"articles": [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}})

    client = ExportClient()
    result = GuideTools(client).export_articles("en-us")

    assert result["data"] == {"articles": [{"id": 1}, {"id": 2}], "truncated": False}
    assert client.paths == [
        ("/api/v2/help_center/en-us/articles.json", {"page[size]": "100"}),
        ("/api/v2/help_center/en-us/articles.json", {"page[size]": "100", "page[after]": "next"}),
    ]


def test_article_export_writes_a_managed_artifact(tmp_path):
    class ExportClient:
        def get(self, path, *, params=None):
            return success({"articles": [{"id": 1, "title": "Welcome"}], "meta": {"has_more": False}})

    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    result = GuideTools(ExportClient(), settings).export_article_artifact("en-us", output_format="json")

    assert result["data"]["format"] == "json"
    assert result["data"]["item_count"] == 1
    assert "articles" not in result["data"]
    assert Path(result["data"]["cache_path"]).read_text() == '[{"id":1,"title":"Welcome"}]'


def test_csat_adapters_only_send_their_official_filters():
    client = StubClient(); tools = GuideTools(client)

    tools.list_csat("legacy", score="good", created_at_start="2026-09-01T00:00:00Z", created_at_end="2026-09-02T00:00:00Z")
    tools.list_csat("survey", ticket_id=9, responder_ids=[3, 4], created_at_start="2026-09-01T00:00:00+00:00")

    assert client.paths == [
        ("/api/v2/satisfaction_ratings.json", {"score": "good", "start_time": "1788220800", "end_time": "1788307200"}),
        ("/api/v2/guide/survey_responses.json", {"filter[subject_zrns]": "zen:ticket:9", "filter[responder_ids]": "3,4", "filter[created_at_start]": "1788220800000"}),
    ]


def test_csat_export_writes_a_managed_artifact(tmp_path):
    class CsatClient:
        def get(self, path, *, params=None):
            assert path == "/api/v2/satisfaction_ratings.json"
            return success({"satisfaction_ratings": [{"id": 1, "score": "good"}]})

    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    result = GuideTools(CsatClient(), settings).export_csat("legacy", output_format="csv")

    assert result["data"]["format"] == "csv"
    assert result["data"]["item_count"] == 1
    assert "satisfaction_ratings" not in result["data"]
    assert (tmp_path / "exports" / str(os.getuid())).exists()
    assert "id,score" in Path(result["data"]["cache_path"]).read_text()


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


def test_article_create_binds_brand_to_approval_and_uses_its_subdomain(tmp_path):
    class BrandClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params))
            return success({"brand": {"subdomain": "brand-one", "has_help_center": True}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            self.paths.append((subdomain, path, params)); return success({"locales": ["en-us"]})
        def request_for_subdomain(self, subdomain, method, path, *, json_body=None):
            self.paths.append((subdomain, method, path, json_body)); return success({"article": {"id": "guide-1"}})

    client = BrandClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = GuideTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}), store)
    preview = tools.create_article(3, "en-us", "Title", "Body", brand_id=7)
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.create_article(3, "en-us", "Title", "Body", brand_id=7, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert result["data"]["article"]["id"] == "guide-1"
    assert client.paths[-1] == ("brand-one", "POST", "/api/v2/help_center/sections/3/articles.json", {"article": {"title": "Title", "body": "Body", "locale": "en-us", "draft": True}, "notify_subscribers": False})


def test_article_publish_binds_brand_to_approval_and_uses_its_subdomain(tmp_path):
    class BrandClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params)); return success({"brand": {"subdomain": "brand-one", "has_help_center": True}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            self.paths.append((subdomain, path, params))
            return success({"locales": ["en-us"]}) if path.endswith("locales.json") else success({"translation": {"id": 6, "draft": True}})
        def request_for_subdomain(self, subdomain, method, path, *, json_body=None):
            self.paths.append((subdomain, method, path, json_body)); return success({})

    client = BrandClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = GuideTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.publish_article(3, "en-us", brand_id=7)
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.publish_article(3, "en-us", brand_id=7, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert result["data"]["translation"]["id"] == 6
    assert ("brand-one", "PUT", "/api/v2/help_center/articles/3/translations/en-us.json", {"translation": {"draft": False}}) in client.paths


def test_article_translation_accepts_a_string_help_center_id(tmp_path):
    class TranslationClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params)); return failure(ErrorCode.NOT_FOUND, "missing")

    client = TranslationClient()
    result = GuideTools(client, approvals=ApprovalStore(tmp_path / "approvals.json")).upsert_article_translation("guide-1", "en-us", title="Title", body="Body")

    assert result["data"]["outbound_write"] is False
    assert client.paths == [("/api/v2/help_center/articles/guide-1/translations/en-us.json", None)]


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


def test_upsert_of_published_translation_requires_public_and_destructive_gates(tmp_path):
    client = TranslationClient(existing=True); client.published = True; store = ApprovalStore(tmp_path / "approvals.json")
    tools = GuideTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}), store)
    preview = tools.upsert_article_translation(3, "en-us", title="New")
    token = store.approve(preview["data"]["approval_request_id"])
    blocked = tools.upsert_article_translation(3, "en-us", title="New", execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert preview["data"]["public"] is True
    assert preview["data"]["destructive"] is True
    assert blocked["error"]["code"] == "write_disabled"
    assert not any(path[0] == "PUT" for path in client.paths)
