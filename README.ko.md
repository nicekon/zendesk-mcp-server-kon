# Zendesk MCP 서버 KON

[English](README.md)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

Zendesk용 Model Context Protocol 서버이며,
[통합 Zendesk MCP PRD](docs/superpowers/specs/2026-09-04-unified-zendesk-mcp-design.md)를
단계적으로 구현합니다.

## 현재 상태

현재 pre-release는 다음의 guard된 도메인을 제공합니다.

- `zendesk_get_connection_status`: 비밀값 없이 설정을 보고하고, 자격증명이 있으면
  인증된 Zendesk 사용자를 확인합니다.
- Support: 티켓 조회·검색·건수·export, guard된 수정, 댓글, 매크로 preview/apply,
  metadata, 안전한 첨부 다운로드·검사를 제공합니다.
- Guide·CSAT: locale, category, section, 문서 검색·export, 권한 metadata,
  만족도, draft 문서·번역 workflow를 제공합니다.
- Community: 게시물·댓글·topic, vote, subscription, content tag, badge,
  안전한 user image/badge icon upload workflow를 제공합니다.
- 조건부 Support 도구: ticket audit 시간 추적과 명시적으로 설정한 custom field의
  Git-Zen 링크 추출을 제공합니다.

서버는 기본 `read_only` 모드로 시작합니다. 표준 쓰기는
`ZENDESK_WRITE_MODE=standard`가 필요하고, public·destructive·impersonation·external
upload은 각각의 `ZENDESK_ENABLE_*` gate가 필요합니다. preview/apply 작업은 항상
일회성 로컬 승인을 요구합니다.

```bash
zendesk approve <approval_request_id>
```

첨부 다운로드는 `ticket_id + attachment_id`로 소속과 악성코드 상태를 다시 확인하고
server-managed cache에만 저장합니다. 캐시 위치는 `ZENDESK_ATTACHMENT_CACHE_ROOT`,
안전한 로컬 image upload root는 `ZENDESK_UPLOAD_ROOT`로 설정할 수 있습니다.

## 설치와 설정

패키지를 설치합니다.

```bash
uv sync --group dev
```

Zendesk 자격증명 없이도 서버를 시작할 수 있습니다. API token 인증을 설정하려면
세 환경 변수를 모두 제공합니다.

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "uv",
      "args": ["--directory", "/path/to/zendesk-mcp-server-kon", "run", "zendesk"],
      "env": {
        "ZENDESK_SUBDOMAIN": "your-zendesk-subdomain",
        "ZENDESK_EMAIL": "your-zendesk-email",
        "ZENDESK_API_TOKEN": "your-zendesk-api-token"
      }
    }
  }
}
```

`ZENDESK_API_KEY`는 더 이상 허용하지 않습니다. `ZENDESK_API_TOKEN`으로
이관해야 하며, 서버는 예전 이름을 조용히 사용하지 않고 오류로 알려줍니다.

`ZENDESK_AUTH_MODE`의 기본값은 `auto`입니다. 완전한 OAuth 설정이 있으면 OAuth를,
그렇지 않으면 완전한 API token 설정을 선택합니다. OAuth 설정이 일부만 있으면
오류가 나며 API token으로 fallback하지 않습니다.

`ZENDESK_CAPABILITIES`로 조건부 도메인을 활성화할 수 있습니다. 비활성 도구도
목록에는 남지만 Zendesk 요청 전에 `not_configured`를 반환합니다. `git_zen`을
활성화한 경우에만 `ZENDESK_GIT_ZEN_FIELD_ID`를 설정합니다. 시간 기록은 항상
내부 note와 함께 Zendesk ticket audit metadata에 남깁니다.

## 프롬프트

- `analyze-ticket(ticket_id)`
- `draft-ticket-response(ticket_id)`

프롬프트는 안내 문구만 만듭니다. Zendesk 쓰기를 실행하지 않습니다.

## 개발

```bash
uv run pytest -v
uv build
```

현재 범위는 [foundation 구현 계획](docs/superpowers/plans/2026-09-04-unified-zendesk-mcp-foundation.md),
전체 기능 로드맵은 PRD에서 확인할 수 있습니다.
