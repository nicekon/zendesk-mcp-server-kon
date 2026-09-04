# Zendesk MCP 서버 KON

[English](README.md)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

Zendesk용 Model Context Protocol 서버입니다. 이 브랜치는
[통합 Zendesk MCP PRD](docs/superpowers/specs/2026-09-04-unified-zendesk-mcp-design.md)의
첫 구현 단계로, Support·Guide·CSAT·Community 도구를 추가하기 전에 안전한 시작,
명시적 인증 선택, 단일 HTTP transport를 마련합니다.

## 현재 상태

현재 pre-release에서 제공하는 안전한 도구입니다.

- `zendesk_get_connection_status`: 설정 여부만 알려줍니다. 네트워크 요청을 보내지
  않고 이메일, token, OAuth secret을 반환하지 않습니다.
- `zendesk_list_tickets`, `zendesk_search_tickets`, `zendesk_count_tickets`,
  `zendesk_get_ticket`, `zendesk_get_ticket_conversation`: 읽기 전용 Support
  조회를 제공합니다. 검색은 항상 티켓 레코드로 한정됩니다.

서버는 기본 `read_only` 모드로 시작합니다. Help Center, CSAT, 첨부파일,
Community 도구는 각 구현 단계가 끝날 때까지 의도적으로 노출하지 않습니다.

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
