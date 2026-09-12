import os
import csv
import json
import pytest
from pathlib import Path

from zendesk_mcp_server.contracts import ErrorCode, failure, success
from zendesk_mcp_server.approvals import ApprovalStore
from zendesk_mcp_server.config import Settings
from zendesk_mcp_server.tools.guide import GuideTools


@pytest.mark.parametrize("page", [
    {"articles": [], "meta": {}},
    {"articles": [None], "meta": {"has_more": False}},
    {"articles": [], "meta": {"has_more": True, "after_cursor": "next"}},
])
def test_article_export_rejects_invalid_or_nonprogressing_pages(page):
    class Client:
        def __init__(self): self.calls = 0
        def get(self, path, *, params=None):
            if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us"], "default_locale": "en-us"})
            self.calls += 1
            assert self.calls == 1
            return success(page)
    result = GuideTools(Client()).export_articles("en-us")
    assert result["ok"] is False
    assert result["error"]["code"] == "upstream_error"


class StubClient:
    def __init__(self): self.paths = []
    def get(self, path, *, params=None):
        self.paths.append((path, params))
        if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us"]})
        return success({"articles": [{"id": 3}]})

    def request(self, method, path, *, json_body=None):
        self.paths.append((method, path, json_body)); return success({"article": {"id": 4}})


def test_satisfaction_ratings_resume_cursor():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/satisfaction_ratings.json"
            assert params == {"page[size]": "1", "page[after]": "next"}
            return success({"satisfaction_ratings": [{"id": 9}], "meta": {"has_more": False}})
    assert GuideTools(Client()).get_satisfaction_ratings(limit=1, cursor="next") == {"ok": True, "items": [{"id": 9}], "has_more": False, "next_cursor": None, "truncated": False, "untrusted_user_content": True}


def test_article_search_resumes_and_stops_at_1000_results():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/help_center/articles/search.json"
            assert params == {"query": "billing", "per_page": "100", "page": "10"}
            return success({"results": [{"id": i} for i in range(900, 1000)], "next_page": "https://untrusted.example/ignored"})
    t = GuideTools(Client())
    assert t.search_articles("billing", limit=1, cursor="999") == {"ok": True, "items": [{"id": 999}], "has_more": True, "next_cursor": None, "truncated": True}
    assert t.search_articles("billing", cursor="1000")["error"]["code"] == "validation_error"


def test_user_segments_preserve_filter_across_pages():
    for applicable in (False, True):
        class Client:
            def get(self, path, *, params=None):
                assert path == ("/api/v2/help_center/user_segments/applicable.json" if applicable else "/api/v2/help_center/user_segments.json")
                assert params["built_in"] == "false"
                more = "page[after]" not in params
                assert params["page[size]"] == ("2" if more else "1")
                if not more: assert params["page[after]"] == "next"
                return success({"user_segments": [{"id": 1 if more else 2}], "meta": {"has_more": more, "after_cursor": "next"}})
        assert GuideTools(Client()).list_user_segments(built_in=False, applicable=applicable, limit=2) == {"ok": True, "items": [{"id": 1}, {"id": 2}], "has_more": False, "next_cursor": None, "truncated": False}


def test_permission_groups_resume_offset():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/guide/permission_groups.json"
            assert params == {"per_page": "100", "page": "2"}
            return success({"permission_groups": [{"id": 9}], "next_page": None})
    assert GuideTools(Client()).list_permission_groups(limit=1, cursor="100") == {"ok": True, "items": [{"id": 9}], "has_more": False, "next_cursor": None, "truncated": False}


def test_guide_category_and_section_lists_resume():
    for key in ("categories", "sections"):
        class Client:
            def get(self, path, *, params=None):
                assert path == f"/api/v2/help_center/{key}.json"
                assert params == {"page[size]": "1", "page[after]": "next"}
                return success({key: [{"id": 9}], "meta": {"has_more": False}})
        assert getattr(GuideTools(Client()), f"list_{key}")(limit=1, cursor="next") == {"ok": True, "items": [{"id": 9}], "has_more": False, "next_cursor": None, "truncated": False}


@pytest.mark.parametrize("key", ["categories", "sections"])
@pytest.mark.parametrize("brand_id", [None, 7])
def test_navigation_locale_uses_selected_brand_and_preserves_cursor(key, brand_id):
    calls = []
    class Client:
        def get(self, path, *, params=None):
            if path == "/api/v2/brands/7.json":
                return success({"brand": {"id": 7, "subdomain": "brand-one", "has_help_center": True}})
            assert brand_id is None
            return self.get_for_subdomain(None, path, params=params)
        def get_for_subdomain(self, subdomain, path, *, params=None):
            calls.append((subdomain, path, params))
            if path == "/api/v2/help_center/locales.json":
                return success({"locales": ["ko"]})
            return success({key: [{"id": "guide-1", "locale": "ko"}], "meta": {"has_more": False}})
    result = getattr(GuideTools(Client()), f"list_{key}")(brand_id=brand_id, locale="ko", limit=1, cursor="next")
    assert result["items"] == [{"id": "guide-1", "locale": "ko"}]
    host = "brand-one" if brand_id else None
    assert calls == [(host, "/api/v2/help_center/locales.json", None),
                     (host, f"/api/v2/help_center/ko/{key}.json", {"page[size]": "1", "page[after]": "next"})]


@pytest.mark.parametrize("key", ["categories", "sections"])
@pytest.mark.parametrize("locale", ["", "../en-us", "en-us?x=1", 3, False])
def test_navigation_rejects_malformed_locale_before_requests(key, locale):
    client = StubClient()
    result = getattr(GuideTools(client), f"list_{key}")(brand_id=7, locale=locale)
    assert result["error"]["code"] == "validation_error"
    assert client.paths == []


@pytest.mark.parametrize("key", ["categories", "sections"])
def test_navigation_rejects_disabled_locale_before_listing(key):
    client = StubClient()
    result = getattr(GuideTools(client), f"list_{key}")(locale="ko")
    assert result["error"]["code"] == "validation_error"
    assert client.paths == [("/api/v2/help_center/locales.json", None)]


@pytest.mark.parametrize("key,scope,path", [("categories", {}, "categories"), ("sections", {}, "sections"), ("sections", {"category_id": "cat-A"}, "categories/cat-A/sections")])
@pytest.mark.parametrize("sort_by", ["position", "created_at", "updated_at"])
@pytest.mark.parametrize("sort_order", ["asc", "desc"])
def test_navigation_sort_and_category_scope_survive_every_page(key, scope, path, sort_by, sort_order):
    seen = []
    class Client:
        def get(self, endpoint, *, params=None):
            assert endpoint == "/api/v2/brands/7.json"
            return success({"brand": {"subdomain": "one", "has_help_center": True}})
        def get_for_subdomain(self, subdomain, endpoint, *, params=None):
            assert subdomain == "one"
            if endpoint.endswith("locales.json"): return success({"locales": ["ko"]})
            assert endpoint == f"/api/v2/help_center/ko/{path}.json"
            seen.append(params)
            more = "page[after]" not in params
            return success({key: [{"id": "one" if more else "two"}], "meta": {"has_more": more, "after_cursor": "next"}})
    result = getattr(GuideTools(Client()), f"list_{key}")(brand_id=7, locale="ko", sort_by=sort_by, sort_order=sort_order, limit=2, **scope)
    assert result["items"] == [{"id": "one"}, {"id": "two"}]
    assert seen == [{"sort_by": sort_by, "sort_order": sort_order, "page[size]": "2"}, {"sort_by": sort_by, "sort_order": sort_order, "page[size]": "1", "page[after]": "next"}]


@pytest.mark.parametrize("args", [{"sort_by": "title"}, {"sort_by": []}, {"sort_order": "DESC"}, {"category_id": ".."}, {"category_id": False}])
def test_navigation_invalid_filter_is_rejected_before_brand_lookup(args):
    client = StubClient()
    result = GuideTools(client).list_sections(brand_id=7, **args)
    assert result["error"]["code"] == "validation_error"
    assert client.paths == []


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
        ("/api/v2/help_center/categories.json", {"page[size]": "100"}),
        ("/api/v2/help_center/sections.json", {"page[size]": "100"}),
        ("/api/v2/help_center/articles/search.json", {"query": "billing", "per_page": "100", "page": "1"}),
        ("/api/v2/help_center/articles/3.json", None),
        ("/api/v2/satisfaction_ratings.json", {"page[size]": "100"}),
        ("/api/v2/guide/permission_groups.json", {"per_page": "100", "page": "1"}),
        ("/api/v2/help_center/user_segments.json", {"built_in": "false", "page[size]": "100"}),
    ]


def test_guide_category_read_resolves_brand_id_to_its_subdomain():
    class BrandClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params))
            return success({"brand": {"id": 7, "subdomain": "brand-one", "has_help_center": True}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            self.paths.append((subdomain, path, params))
            return success({"categories": [{"id": "guide-1"}], "meta": {"has_more": False}})

    client = BrandClient()
    result = GuideTools(client).list_categories(brand_id=7)

    assert result["items"] == [{"id": "guide-1"}]
    assert client.paths == [
        ("/api/v2/brands/7.json", None),
        ("brand-one", "/api/v2/help_center/categories.json", {"page[size]": "100"}),
    ]


def test_article_read_embeds_only_tenant_help_center_images_with_a_total_limit():
    class ImageClient:
        def __init__(self): self.downloads = []
        def get(self, path, *, params=None):
            return success({"article": {"id": 3, "body": '<img src="https://acme.zendesk.com/hc/user_images/one.png"><img src="https://outside.example/image.png">'}})
        def download_help_center_image(self, url, *, max_bytes, subdomain=None):
            self.downloads.append((url, max_bytes, subdomain))
            return success({"content": b"image", "content_type": "image/png", "size": 5})

    client = ImageClient()
    result = GuideTools(client).get_article(3, embed_images=True)

    assert result["data"]["images"] == [{"src": "https://acme.zendesk.com/hc/user_images/one.png", "content": b"image", "content_type": "image/png"}]
    assert client.downloads == [("https://acme.zendesk.com/hc/user_images/one.png", 20 * 1024 * 1024, None)]


@pytest.mark.parametrize("brand_ids", [None, [7, 8]])
@pytest.mark.parametrize("query", [None, "billing"])
def test_unified_article_search_preserves_all_filters_and_cursor(brand_ids, query):
    searches = []
    class Client:
        def get(self, path, *, params=None):
            if path == "/api/v2/brands.json":
                return success({"brands": [{"id": 7, "has_help_center": True}, {"id": 8, "has_help_center": True}], "meta": {"has_more": False}})
            if path.startswith("/api/v2/brands/"):
                return success({"brand": {"subdomain": "one" if path.endswith("7.json") else "two", "has_help_center": True}})
            assert path == "/api/v2/guide/search"
            searches.append(params)
            more = params["page[after]"] == "start"
            return success({"results": [{"id": "article-A", "type": "ARTICLE"}], "meta": {"has_more": more, "after_cursor": "next"}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            assert path == "/api/v2/help_center/locales.json"
            return success({"locales": ["en-us"] if subdomain == "one" else ["ko"]})
    result = GuideTools(Client()).search_articles(query, locales=["en-us", "ko"], brand_ids=brand_ids, category_ids=[9, "cat-A"], section_ids=["sec-B"], limit=51, cursor="start")
    assert result["ok"] is True and len(result["items"]) == 2
    expected = {"filter[locales]": "en-us,ko", "filter[content_types]": "ARTICLE", "filter[category_ids]": "9,cat-A", "filter[section_ids]": "sec-B"}
    if brand_ids is not None: expected["filter[brand_ids]"] = "7,8"
    if query is not None: expected["query"] = query
    assert searches == [{**expected, "page[size]": "50", "page[after]": "start"}, {**expected, "page[size]": "50", "page[after]": "next"}]


@pytest.mark.parametrize("args", [
    {"locales": []}, {"locales": ["../ko"]}, {"locales": [False]},
    {"locales": ["ko"], "brand_ids": [False]},
    {"locales": ["ko"], "category_ids": ["1,2"]},
    {"locales": ["ko"], "section_ids": []},
    {"locales": ["ko"], "locale": "ko"},
    {"locales": ["ko"], "brand_id": 7},
    {"locales": ["ko"], "query": "x" * 501},
    {"brand_ids": [7]},
])
def test_unified_search_rejects_invalid_or_mixed_inputs_before_network(args):
    client = StubClient()
    result = GuideTools(client).search_articles(**args)
    assert result["error"]["code"] == "validation_error"
    assert client.paths == []


def test_unified_search_rejects_locale_disabled_in_selected_brands():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/brands/7.json"
            return success({"brand": {"subdomain": "one", "has_help_center": True}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            assert path == "/api/v2/help_center/locales.json"
            return success({"locales": ["en-us"]})
    assert GuideTools(Client()).search_articles(locales=["ko"], brand_ids=[7])["error"]["code"] == "validation_error"


def test_unified_search_checks_every_explicit_brand_even_after_locale_match():
    denied = failure(ErrorCode.PERMISSION_DENIED, "denied", request_id="brand-access")
    class Client:
        def get(self, path, *, params=None):
            if path == "/api/v2/brands/8.json": return denied
            assert path == "/api/v2/brands/7.json"
            return success({"brand": {"subdomain": "one", "has_help_center": True}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            return success({"locales": ["ko"]})
    for brands in ([7, 8], [8, 7]):
        assert GuideTools(Client()).search_articles(locales=["ko"], brand_ids=brands) == denied


def test_guide_search_uses_official_brand_and_locale_filters():
    class SearchClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params))
            return success({"brand": {"subdomain": "brand-one", "has_help_center": True}}) if path.startswith("/api/v2/brands/") else success({"articles": []})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            self.paths.append((subdomain, path, params)); return success({"locales": ["en-us"]})

    client = SearchClient()

    GuideTools(client).search_articles("billing", brand_id=7, locale="en-us")

    assert client.paths == [
        ("/api/v2/brands/7.json", None),
        ("brand-one", "/api/v2/help_center/locales.json", None),
        ("/api/v2/help_center/articles/search.json", {"query": "billing", "brand_id": "7", "locale": "en-us", "per_page": "100", "page": "1"}),
    ]


def test_all_article_translations_are_brand_scoped_paginated_and_share_image_budget():
    downloads = []
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/brands/7.json"
            return success({"brand": {"subdomain": "one", "has_help_center": True}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            assert subdomain == "one"
            if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us", "ko"]})
            assert path == "/api/v2/help_center/articles/art-A/translations.json"
            assert params["locales"] == "en-us,ko"
            more = "page[after]" not in params
            if not more: assert params["page[after]"] == "next"
            locale = "en-us" if more else "ko"
            return success({"translations": [{"id": locale, "locale": locale, "body": f'<img src="https://one.zendesk.com/hc/user_images/{locale}.png">'}], "meta": {"has_more": more, "after_cursor": "next"}})
        def download_help_center_image(self, url, *, max_bytes, subdomain):
            downloads.append((url, max_bytes, subdomain))
            return success({"content": b"abc", "content_type": "image/png"})
    result = GuideTools(Client()).get_article("art-A", brand_id=7, locale="all", embed_images=True)
    assert result["ok"] is True
    assert [item["locale"] for item in result["data"]["translations"]] == ["en-us", "ko"]
    assert all(item["untrusted_user_content"] for item in result["data"]["translations"])
    assert [entry[1] for entry in downloads] == [20 * 1024 * 1024, 20 * 1024 * 1024 - 3]
    assert all(entry[2] == "one" for entry in downloads)


@pytest.mark.parametrize("locale", [None, "ko", "all"])
def test_article_metadata_names_are_joined_by_ids_not_positions(locale):
    class Client:
        def get(self, path, *, params=None):
            if path == "/api/v2/help_center/locales.json": return success({"locales": ["ko"]})
            if path.endswith("/translations/ko.json"): return success({"translation": {"locale": "ko", "body": "body"}})
            if path.endswith("/translations.json"): return success({"translations": [{"locale": "ko", "body": "body"}], "meta": {"has_more": False}})
            assert path == "/api/v2/help_center/articles/art-A.json"
            assert params == {"include": "users,sections,categories"}
            return success({"article": {"id": "art-A", "section_id": "s", "author_id": 9}, "sections": [{"id": "other", "name": "Wrong"}, {"id": "s", "category_id": "c", "name": "Section"}], "categories": [{"id": "c", "name": "Category"}], "users": [{"id": 8, "name": "Wrong"}, {"id": 9, "name": "Author"}]})
    result = GuideTools(Client()).get_article("art-A", locale=locale, include_metadata=True)
    assert result["ok"] is True
    assert result["data"]["metadata"] == {"section_name": "Section", "category_name": "Category", "author_name": "Author"}


def test_all_translation_read_does_not_return_partial_success_on_later_failure():
    denied = failure(ErrorCode.PERMISSION_DENIED, "denied", request_id="translations")
    class Client:
        def get(self, path, *, params=None):
            if path.endswith("locales.json"): return success({"locales": ["ko"]})
            if "page[after]" in params: return denied
            return success({"translations": [{"locale": "ko", "body": "body"}], "meta": {"has_more": True, "after_cursor": "next"}})
    assert GuideTools(Client()).get_article("a", locale="all") == denied


@pytest.mark.parametrize("returned_locale", [None, "en-us"])
def test_all_translation_read_rejects_unrequested_upstream_locale(returned_locale):
    class Client:
        def get(self, path, *, params=None):
            if path.endswith("locales.json"): return success({"locales": ["ko"]})
            return success({"translations": [{"locale": returned_locale, "body": "body"}], "meta": {"has_more": False}})
    result = GuideTools(Client()).get_article("a", locale="all")
    assert result["error"]["code"] == "upstream_error"


def test_article_read_validates_locale_and_uses_the_translation_endpoint():
    class TranslationClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            self.paths.append((path, params))
            return success({"locales": ["en-us"]}) if path.endswith("locales.json") else success({"translation": {"locale": "en-us", "body": "Body"}})

    client = TranslationClient()
    result = GuideTools(client).get_article(3, locale="en-us")

    assert result["data"]["translation"]["body"] == "Body"
    assert client.paths == [("/api/v2/help_center/locales.json", None), ("/api/v2/help_center/articles/3/translations/en-us.json", None)]


def test_translation_read_embeds_validated_images_when_requested():
    class TranslationImageClient:
        def get(self, path, *, params=None):
            return success({"locales": ["en-us"]}) if path.endswith("locales.json") else success({"translation": {"body": '<img src="https://acme.zendesk.com/hc/user_images/one.png">'}})
        def download_help_center_image(self, url, *, max_bytes, subdomain=None):
            return success({"content": b"image", "content_type": "image/png", "size": 5})

    result = GuideTools(TranslationImageClient()).get_article(3, locale="en-us", embed_images=True)

    assert result["data"]["images"][0]["content"] == b"image"


def test_help_center_content_is_marked_untrusted():
    class ArticleClient:
        def get(self, path, *, params=None): return success({"article": {"id": 3, "body": "Body"}})

    result = GuideTools(ArticleClient()).get_article(3)

    assert result["data"]["article"]["untrusted_user_content"] is True


def test_guide_search_rejects_a_disabled_locale_before_search():
    client = StubClient()

    result = GuideTools(client).search_articles("billing", locale="ko")

    assert result["error"]["code"] == "validation_error"
    assert client.paths == [("/api/v2/help_center/locales.json", None)]


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


@pytest.mark.parametrize("scope,path_part", [({}, ""), ({"category_id": "cat-A"}, "categories/cat-A/"), ({"section_id": 9}, "sections/9/"), ({"category_id": "cat-A", "section_id": "sec-B"}, "sections/sec-B/")])
@pytest.mark.parametrize("locale", [None, "ko"])
@pytest.mark.parametrize("artifact", [False, True])
def test_scoped_article_export_resolves_brand_default_and_keeps_scope(tmp_path, scope, path_part, locale, artifact):
    calls = []
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/brands/7.json"
            return success({"brand": {"subdomain": "one", "has_help_center": True}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            assert subdomain == "one"
            calls.append((path, params))
            if path == "/api/v2/help_center/locales.json":
                return success({"locales": ["en-us", "ko"], "default_locale": "ko"})
            assert path == f"/api/v2/help_center/ko/{path_part}articles.json"
            more = "page[after]" not in params
            return success({"articles": [{"id": "a" if more else "b", "body": "<p>본문</p>"}], "meta": {"has_more": more, "after_cursor": "next"}})
    tools = GuideTools(Client(), Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")}))
    result = (tools.export_article_artifact if artifact else tools.export_articles)(locale, max_articles=2, brand_id=7, **scope)
    assert result["ok"] is True
    items = json.loads(Path(result["data"]["cache_path"]).read_text()) if artifact else result["data"]["articles"]
    assert items == [{"id": "a", "body": "<p>본문</p>"}, {"id": "b", "body": "<p>본문</p>"}]
    assert calls == [("/api/v2/help_center/locales.json", None), (f"/api/v2/help_center/ko/{path_part}articles.json", {"page[size]": "2"}), (f"/api/v2/help_center/ko/{path_part}articles.json", {"page[size]": "1", "page[after]": "next"})]


@pytest.mark.parametrize("locale,data,code", [
    (None, {"locales": ["ko"]}, "upstream_error"),
    (None, {"locales": ["ko"], "default_locale": "../ko"}, "upstream_error"),
    (None, {"locales": ["ko"], "default_locale": "en-us"}, "upstream_error"),
    ("en-us", {"locales": ["ko"]}, "validation_error"),
])
def test_article_export_rejects_unknown_default_or_disabled_locale(locale, data, code):
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/help_center/locales.json"
            return success(data)
    assert GuideTools(Client()).export_articles(locale)["error"]["code"] == code


def test_article_export_rejects_invalid_scope_before_brand_lookup():
    client = StubClient()
    for scope in ({"section_id": False}, {"category_id": 0}, {"section_id": ""}):
        assert GuideTools(client).export_articles(brand_id=7, **scope)["error"]["code"] == "validation_error"
    assert client.paths == []


@pytest.mark.parametrize("identifier", [".", ".."])
def test_help_center_dot_segment_ids_never_reach_the_client(identifier):
    client = StubClient()
    tools = GuideTools(client)
    assert tools.get_article(identifier)["error"]["code"] == "validation_error"
    assert tools.export_articles("en-us", section_id=identifier)["error"]["code"] == "validation_error"
    assert tools.export_articles("en-us", category_id=identifier)["error"]["code"] == "validation_error"
    assert client.paths == []


def test_article_export_uses_locale_cursor_pagination():
    class ExportClient:
        def __init__(self): self.paths = []
        def get(self, path, *, params=None):
            if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us"], "default_locale": "en-us"})
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


def test_article_export_enforces_local_limit_even_when_upstream_ignores_page_size():
    class OversizedPageClient:
        def get(self, path, *, params=None):
            if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us"], "default_locale": "en-us"})
            assert params["page[size]"] == "1"
            return success({"articles": [{"id": 1}, {"id": 2}], "meta": {"has_more": False}})

    result = GuideTools(OversizedPageClient()).export_articles("en-us", max_articles=1)
    assert result["data"] == {"articles": [{"id": 1}], "truncated": True}


def test_article_export_empty_final_page_and_repeated_cursor():
    for repeat in (False, True):
        class PagesClient:
            calls = 0
            def get(self, path, *, params=None):
                if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us"], "default_locale": "en-us"})
                self.calls += 1
                if self.calls == 1:
                    return success({"articles": [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}})
                return success({"articles": [], "meta": {"has_more": repeat, "after_cursor": "next"}})

        client = PagesClient()
        result = GuideTools(client).export_articles("en-us")
        assert client.calls == 2
        if repeat:
            assert result["error"]["code"] == "upstream_error"
        else:
            assert result["data"] == {"articles": [{"id": 1}], "truncated": False}


def test_article_export_writes_a_managed_artifact(tmp_path):
    class ExportClient:
        def get(self, path, *, params=None):
            if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us"], "default_locale": "en-us"})
            return success({"articles": [{"id": 1, "title": "Welcome"}], "meta": {"has_more": False}})

    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    result = GuideTools(ExportClient(), settings).export_article_artifact("en-us", output_format="json")

    assert result["data"]["format"] == "json"
    assert result["data"]["item_count"] == 1
    assert "articles" not in result["data"]
    assert Path(result["data"]["cache_path"]).read_text() == '[{"id":1,"title":"Welcome"}]'


def test_article_artifact_streams_pages_and_preserves_late_csv_columns(tmp_path):
    import weakref
    class Article(dict): pass
    class ExportClient:
        previous = None
        def get(self, path, *, params=None):
            if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us"], "default_locale": "en-us"})
            if params.get("page[after]") == "next":
                assert self.previous() is None, "previous page is still retained"
                return success({"articles": [{"id": 2, "late": "추가"}], "meta": {"has_more": False}})
            article = Article(id=1, title="Welcome")
            self.previous = weakref.ref(article)
            return success({"articles": [article], "meta": {"has_more": True, "after_cursor": "next"}})
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    for format in ("json", "csv"):
        result = GuideTools(ExportClient(), settings).export_article_artifact("en-us", output_format=format)
        assert result["data"]["item_count"] == 2
        assert result["data"]["truncated"] is False
        with Path(result["data"]["cache_path"]).open() as stream:
            if format == "json": assert json.load(stream) == [{"id": 1, "title": "Welcome"}, {"id": 2, "late": "추가"}]
            else: assert list(csv.DictReader(stream)) == [{"id": "1", "title": "Welcome", "late": ""}, {"id": "2", "title": "", "late": "추가"}]


@pytest.mark.parametrize("output_format", ["json", "csv"])
def test_article_export_does_not_buffer_the_serialized_artifact(tmp_path, output_format):
    import tracemalloc
    class Client:
        def get(self, path, *, params=None):
            if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us"], "default_locale": "en-us"})
            start = int(params.get("page[after]", "0"))
            end = min(10000, start + int(params["page[size]"]))
            return success({"articles": [{"id": i, "body": str(i) + "x" * 1024} for i in range(start, end)], "meta": {"has_more": end < 10000, "after_cursor": str(end)}})
    tools = GuideTools(Client(), Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")}))
    tracemalloc.start()
    try:
        result = tools.export_article_artifact("en-us", max_articles=10000, output_format=output_format)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["ok"] is True
    assert result["data"]["item_count"] == 10000
    assert result["data"]["truncated"] is False
    assert Path(result["data"]["cache_path"]).stat().st_size == result["data"]["size"] > 10_000_000
    assert peak < 4_000_000, "export retained page bodies or the serialized file in memory"


def test_article_export_failure_does_not_publish_partial_artifact(tmp_path):
    class ExportClient:
        def get(self, path, *, params=None):
            if path == "/api/v2/help_center/locales.json": return success({"locales": ["en-us"], "default_locale": "en-us"})
            if params.get("page[after]"):
                return failure(ErrorCode.PERMISSION_DENIED, "denied")
            return success({"articles": [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}})
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    result = GuideTools(ExportClient(), settings).export_article_artifact("en-us")
    assert result["error"]["code"] == "permission_denied"
    assert not list(tmp_path.rglob("*export*"))


def test_csat_results_mark_customer_content_untrusted(tmp_path):
    class Client:
        def get(self, path, *, params=None):
            key = "satisfaction_ratings" if "satisfaction_ratings" in path else "survey_responses"
            return success({key: [{"id": 1, "comment": "Customer text"}], "meta": {"has_more": False}})
    tools = GuideTools(Client(), Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")}))
    assert tools.get_satisfaction_ratings()["untrusted_user_content"] is True
    for backend in ("legacy", "survey"):
        listed = tools.list_csat(backend)
        assert listed["untrusted_user_content"] is True
        assert listed["items"][0]["comment"] == "Customer text"
        assert tools.export_csat(backend)["data"]["untrusted_user_content"] is True


def test_csat_lists_preserve_backend_filters_across_pages():
    for backend, key, filters in (("legacy", "satisfaction_ratings", {"score": "good"}), ("survey", "survey_responses", {"ticket_id": 9})):
        class Client:
            def get(self, path, *, params=None):
                assert path == ("/api/v2/satisfaction_ratings.json" if backend == "legacy" else "/api/v2/guide/survey_responses")
                assert params.get("score") == ("good" if backend == "legacy" else None)
                assert params.get("filter[subject_zrns]") == ("zen:ticket:9" if backend == "survey" else None)
                more = "page[after]" not in params
                assert params["page[size]"] == ("2" if more else "1")
                if not more: assert params["page[after]"] == "next"
                return success({key: [{"id": 1 if more else 2}], "meta": {"has_more": more, "after_cursor": "next"}})
        assert GuideTools(Client()).list_csat(backend, limit=2, **filters) == {"ok": True, "items": [{"id": 1}, {"id": 2}], "has_more": False, "next_cursor": None, "truncated": False, "untrusted_user_content": True}


def test_csat_adapters_only_send_their_official_filters():
    client = StubClient(); tools = GuideTools(client)

    tools.list_csat("legacy", score="good", start_time="2026-09-01T00:00:00Z", end_time="2026-09-02T00:00:00Z")
    tools.list_csat("survey", ticket_id=9, responder_ids=[3, 4], created_at_start="2026-09-01T00:00:00+00:00")

    assert client.paths == [
        ("/api/v2/satisfaction_ratings.json", {"page[size]": "100", "score": "good", "start_time": "1788220800", "end_time": "1788307200"}),
        ("/api/v2/guide/survey_responses", {"page[size]": "50", "filter[subject_zrns]": "zen:ticket:9", "filter[responder_ids]": "3,4", "filter[created_at_start]": "1788220800000"}),
    ]


def test_csat_date_families_and_timezone_are_validated_for_both_consumers(tmp_path):
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    for method in ("list_csat", "export_csat"):
        for backend, dates in (("legacy", {"created_at_start": "2026-09-01T00:00:00Z"}), ("survey", {"start_time": "2026-09-01T00:00:00Z"}), ("legacy", {"start_time": "2026-09-01T00:00:00"}), ("legacy", {"start_time": "2026-09-02T00:00:00Z", "end_time": "2026-09-01T00:00:00Z"})):
            assert getattr(GuideTools(None, settings), method)(backend, **dates)["error"]["code"] == "validation_error"


@pytest.mark.parametrize("backend,key", [("legacy", "satisfaction_ratings"), ("survey", "survey_responses")])
@pytest.mark.parametrize("bad_page,more", [([None], False), ([], True)])
def test_csat_export_rejects_bad_later_page_without_publishing(tmp_path, backend, key, bad_page, more):
    class Client:
        def __init__(self): self.calls = 0
        def get(self, path, *, params=None):
            self.calls += 1
            assert self.calls <= 2
            if self.calls == 1:
                return success({key: [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}})
            return success({key: bad_page, "meta": {"has_more": more, "after_cursor": "another"}})
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    result = GuideTools(Client(), settings).export_csat(backend)
    assert result["ok"] is False and result["error"]["code"] == "upstream_error"
    assert not list(tmp_path.rglob("*.json"))


def test_survey_list_and_export_use_official_suffixless_path(tmp_path):
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/guide/survey_responses"
            return success({"survey_responses": [], "meta": {"has_more": False}})
    tools = GuideTools(Client(), Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")}))
    assert tools.list_csat("survey", limit=1)["ok"] is True
    assert tools.export_csat("survey")["ok"] is True


@pytest.mark.parametrize("backend", ["auto", "survey"])
def test_survey_page_cap_preserves_list_and_export_totals(tmp_path, backend):
    class Client:
        def __init__(self): self.sizes = []
        def get(self, path, *, params=None):
            if path == "/api/v2/account/settings.json":
                return success({"settings": {"active_features": {"customer_satisfaction": False, "customer_satisfaction_survey": True}}})
            assert path == "/api/v2/guide/survey_responses"
            size = int(params["page[size]"])
            assert 1 <= size <= 50
            self.sizes.append(size)
            start = int(params.get("page[after]", "0"))
            end = min(101, start + size)
            return success({"survey_responses": [{"id": i} for i in range(start, end)], "meta": {"has_more": end < 101, "after_cursor": str(end)}})
    client = Client()
    tools = GuideTools(client, Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")}))
    expected = [{"id": i} for i in range(101)]
    result = tools.list_csat(backend, limit=101)
    assert result["ok"] is True and result["items"] == expected
    assert client.sizes == [50, 50, 1]
    client.sizes.clear()
    result = tools.export_csat(backend)
    assert result["data"]["item_count"] == 101 and result["data"]["truncated"] is False
    assert json.loads(Path(result["data"]["cache_path"]).read_text()) == expected
    assert client.sizes == [50, 50, 50]


@pytest.mark.parametrize("backend", ["legacy", "survey"])
@pytest.mark.parametrize("operation", ["list", "export"])
def test_csat_auto_uses_account_features_once_per_operation(tmp_path, backend, operation):
    calls = []
    key = "satisfaction_ratings" if backend == "legacy" else "survey_responses"
    endpoint = "/api/v2/satisfaction_ratings.json" if backend == "legacy" else "/api/v2/guide/survey_responses"
    class Client:
        def get(self, path, *, params=None):
            calls.append(path)
            if path == "/api/v2/account/settings.json":
                return success({"settings": {"active_features": {"customer_satisfaction": backend == "legacy", "customer_satisfaction_survey": backend == "survey"}}})
            assert path == endpoint
            more = "page[after]" not in params
            return success({key: [{"id": 1 if more else 2}], "meta": {"has_more": more, "after_cursor": "next"}})
    tools = GuideTools(Client(), Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")}))
    result = tools.list_csat(limit=2) if operation == "list" else tools.export_csat()
    assert result["ok"] is True
    items = result["items"] if operation == "list" else json.loads(Path(result["data"]["cache_path"]).read_text())
    assert items == [{"id": 1}, {"id": 2}]
    assert calls == ["/api/v2/account/settings.json", endpoint, endpoint]


@pytest.mark.parametrize("operation", ["list", "export"])
@pytest.mark.parametrize("settings_result,code", [
    (failure(ErrorCode.PERMISSION_DENIED, "denied", request_id="detect-request"), "permission_denied"),
    (success({"settings": {"active_features": {"customer_satisfaction": False, "customer_satisfaction_survey": False}}}), "unsupported"),
    (success({"settings": {"active_features": {"customer_satisfaction": True, "customer_satisfaction_survey": True}}}), "unsupported"),
    (success({"settings": {"active_features": {"customer_satisfaction": "false", "customer_satisfaction_survey": True}}}), "upstream_error"),
    (success({"settings": {}}), "upstream_error"),
])
def test_csat_auto_does_not_guess_or_fallback_when_detection_fails(tmp_path, operation, settings_result, code):
    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append(path)
            return settings_result
    tools = GuideTools(Client(), Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")}))
    result = tools.list_csat() if operation == "list" else tools.export_csat()
    assert result["error"]["code"] == code
    if not settings_result["ok"]: assert result == settings_result
    assert calls == ["/api/v2/account/settings.json"]


def test_csat_auto_never_uses_filter_as_backend_detection():
    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append(path)
            return success({"settings": {"active_features": {"customer_satisfaction": False, "customer_satisfaction_survey": True}}})
    result = GuideTools(Client()).list_csat(score="good")
    assert result["error"]["code"] == "validation_error"
    assert calls == ["/api/v2/account/settings.json"]


def test_csat_export_writes_a_managed_artifact(tmp_path):
    class CsatClient:
        def get(self, path, *, params=None):
            assert path == "/api/v2/satisfaction_ratings.json"
            return success({"satisfaction_ratings": [{"id": 1, "score": "good"}], "meta": {"has_more": False}})

    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    result = GuideTools(CsatClient(), settings).export_csat("legacy", output_format="csv")

    assert result["data"]["format"] == "csv"
    assert result["data"]["item_count"] == 1
    assert "satisfaction_ratings" not in result["data"]
    assert (tmp_path / "exports" / str(os.getuid())).exists()
    assert "id,score" in Path(result["data"]["cache_path"]).read_text()


def test_csat_export_follows_cursors_without_retaining_pages(tmp_path):
    import weakref
    class Rating(dict): pass
    for backend, key in (("legacy", "satisfaction_ratings"), ("survey", "survey_responses")):
        class CsatClient:
            previous = None
            def get(self, path, *, params=None):
                assert params["page[size]"] == ("100" if backend == "legacy" else "50")
                if params.get("page[after]") == "next":
                    assert self.previous() is None
                    return success({key: [{"id": 2}], "meta": {"has_more": False}})
                item = Rating(id=1)
                self.previous = weakref.ref(item)
                return success({key: [item], "meta": {"has_more": True, "after_cursor": "next"}})
        settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
        result = GuideTools(CsatClient(), settings).export_csat(backend)
        assert result["data"]["item_count"] == 2
        assert result["data"]["truncated"] is False
        assert json.loads(Path(result["data"]["cache_path"]).read_text()) == [{"id": 1}, {"id": 2}]


@pytest.mark.parametrize("backend,key", [("legacy", "satisfaction_ratings"), ("survey", "survey_responses")])
@pytest.mark.parametrize("output_format", ["json", "csv"])
def test_csat_export_bounds_memory_for_large_artifacts(tmp_path, backend, key, output_format):
    import tracemalloc
    class Client:
        def get(self, path, *, params=None):
            start = int(params.get("page[after]", "0"))
            end = min(10000, start + int(params["page[size]"]))
            return success({key: [{"id": i, "comment": str(i) + "x" * 1024} for i in range(start, end)], "meta": {"has_more": end < 10000, "after_cursor": str(end)}})
    tools = GuideTools(Client(), Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")}))
    tracemalloc.start()
    try:
        result = tools.export_csat(backend, output_format=output_format)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["ok"] is True
    assert result["data"]["item_count"] == 10000 and result["data"]["truncated"] is False
    assert Path(result["data"]["cache_path"]).stat().st_size == result["data"]["size"] > 10_000_000
    assert peak < 4_000_000, "CSAT export retained page bodies or the serialized artifact"


def test_csat_export_rejects_repeated_cursor_and_enforces_total_cap(tmp_path):
    class CsatClient:
        oversized = False
        def get(self, path, *, params=None):
            return success({"satisfaction_ratings": [{"id": 1}] * (100001 if self.oversized else 1), "meta": {"has_more": True, "after_cursor": "repeat"}})
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    client = CsatClient(); tools = GuideTools(client, settings)
    rejected = tools.export_csat("legacy")
    assert rejected["error"]["code"] == "upstream_error"
    assert not list(tmp_path.rglob("*.json"))
    client.oversized = True
    result = tools.export_csat("legacy")
    assert result["data"]["item_count"] == 100000
    assert result["data"]["truncated"] is True
    assert len(json.loads(Path(result["data"]["cache_path"]).read_text())) == 100000


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


def test_article_notification_preview_marks_recipient_count_unknown(tmp_path):
    client = StubClient()
    result = GuideTools(client, approvals=ApprovalStore(tmp_path / "approvals.json")).create_article(3, "en-us", "Title", "Body", notify_subscribers=True)

    assert result["data"]["public"] is True
    assert result["data"]["recipient_count_unknown"] is True
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
        def __init__(self): self.paths = []; self.draft = True
        def get(self, path, *, params=None):
            self.paths.append((path, params)); return success({"brand": {"subdomain": "brand-one", "has_help_center": True}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            self.paths.append((subdomain, path, params))
            return success({"locales": ["en-us"]}) if path.endswith("locales.json") else success({"translation": {"id": 6, "locale": "en-us", "draft": self.draft}})
        def request_for_subdomain(self, subdomain, method, path, *, json_body=None):
            self.paths.append((subdomain, method, path, json_body))
            assert subdomain == "brand-one" and method == "PUT" and json_body == {"translation": {"draft": False}}
            self.draft = False
            return success({}, request_id="brand-publish-request", operation_state="applied")

    client = BrandClient(); store = ApprovalStore(tmp_path / "approvals.json")
    tools = GuideTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.publish_article(3, "en-us", brand_id=7)
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.publish_article(3, "en-us", brand_id=7, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert result["data"]["translation"]["id"] == 6
    assert result["operation_state"] == "applied" and result["request_id"] == "brand-publish-request"
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


def test_article_create_accepts_a_string_help_center_section_id(tmp_path):
    tools = GuideTools(StubClient(), approvals=ApprovalStore(tmp_path / "approvals.json"))

    result = tools.create_article("section-1", "en-us", "Title", "Body")

    assert result["data"]["outbound_write"] is False


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
    assert result["operation_state"] == "applied"
    assert client.paths[-4:] == [
        ("/api/v2/help_center/articles/3/translations/en-us.json", None),
        ("/api/v2/help_center/locales.json", None),
        ("PUT", "/api/v2/help_center/articles/3/translations/en-us.json", {"translation": {"draft": False}}),
        ("/api/v2/help_center/articles/3/translations/en-us.json", None),
    ]


@pytest.mark.parametrize("observed", [
    success({"translation": {"locale": "en-us", "draft": True}}),
    success({"translation": {"locale": "ko", "draft": False}}),
    success({"translation": {"locale": "en-us"}}),
    success({}),
    failure(ErrorCode.PERMISSION_DENIED, "read-back forbidden"),
])
def test_publish_read_back_failure_preserves_unknown_write_outcome(tmp_path, observed):
    class Client(TranslationClient):
        def get(self, path, *, params=None):
            if self.published and path.endswith("/translations/en-us.json"):
                return observed
            return super().get(path, params=params)
        def request(self, method, path, *, json_body=None):
            result = super().request(method, path, json_body=json_body)
            return {**result, "request_id": "publish-request", "operation_state": "applied"}
    client = Client(existing=True)
    store = ApprovalStore(tmp_path / "approvals.json")
    tools = GuideTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.publish_article(3, "en-us")
    identifier = preview["data"]["approval_request_id"]
    result = tools.publish_article(3, "en-us", execution_mode="apply", approval_request_id=identifier, approval_token=store.approve(identifier))
    assert result["ok"] is False
    assert result["error"]["code"] == "outcome_unknown"
    assert result["error"]["operation_state"] == "unknown"
    assert result["error"]["request_id"] == "publish-request"
    assert result["error"]["retryable"] is False
    assert sum(item[0] == "PUT" for item in client.paths) == 1


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
