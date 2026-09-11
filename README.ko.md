# Zendesk MCP 서버 KON

[English](README.md)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

Zendesk용 Model Context Protocol 서버이며,
[통합 Zendesk MCP PRD](docs/superpowers/specs/2026-09-04-unified-zendesk-mcp-design.md)를
단계적으로 구현합니다.
[capability manifest](docs/capability-manifest.md)에는 Community 도구가 따르는
Zendesk 공식 API 계약을 기록합니다.
[출시 절차](docs/releasing.md)에는 wheel·MCPB·MCP Registry 배포 순서를 기록합니다.
기존 KON 또는 OAuth 설치를 바꾸기 전에는 [migration·rollback 절차](docs/migration.md)를
확인합니다.

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

이 명령은 MCP 서버와 같은 `ZENDESK_SUBDOMAIN` 환경에서 실행해야 합니다. 승인은
해당 tenant에 결합되므로 다른 tenant에서 재사용할 수 없습니다.

첨부 다운로드는 `ticket_id + attachment_id`로 소속과 악성코드 상태를 다시 확인하고
server-managed cache에만 저장합니다. 캐시 위치는 `ZENDESK_ATTACHMENT_CACHE_ROOT`,
안전한 로컬 image upload root는 `ZENDESK_UPLOAD_ROOT`로 설정할 수 있습니다.

## 설치와 설정

아직 변경 불가능한 사용자용 release는 게시되지 않았습니다. release commit을
push한 뒤 소스 패키지는 다음처럼 설치합니다.

```bash
git clone https://github.com/nicekon/zendesk-mcp-server-kon.git
cd zendesk-mcp-server-kon
uv tool install .
zendesk --help
```

uv가 실행 파일 경로가 `PATH`에 없다고 알리면 `uv tool update-shell`을 실행하고
새 터미널을 엽니다. `uv tool install`은 격리된 환경에 `zendesk` 명령을 설치합니다.
아래의 `uv sync`는 저장소 개발용입니다.

### 브라우저 OAuth 로그인

Zendesk 관리자가 해당 tenant에 **Public** OAuth client를 한 번 만들고 다음 redirect
URI를 정확히 등록합니다.

```text
http://127.0.0.1:3000/oauth/callback
```

client에는 활성 capability가 요청하는 읽기 scope가 허용돼야 합니다. 관리자는
client identifier만 사용자에게 전달하며 client secret은 배포하지 않습니다.
사용자는 다음 명령을 실행합니다.

```bash
zendesk login --subdomain your-zendesk-subdomain --client-id your-client-identifier
zendesk check --probe
```

`zendesk login`은 `127.0.0.1:3000`에서 임시 callback을 열고 시스템 브라우저를
실행합니다. state와 PKCE code 및 Zendesk 사용자를 확인한 뒤 연결을
`~/.config/zendesk-mcp-server/connection.json`에 저장하고 listener를 종료합니다.
사용자가 authorization code를 복사할 필요는 없습니다. `--port`는 같은 대체
redirect URI를 Zendesk에 등록했을 때만 사용합니다.

macOS/Linux에서는 `command -v zendesk`, Windows에서는 `where zendesk`로 설치된
실행 파일을 찾습니다. Codex에는 그 절대 경로를 등록하고 결과를 확인합니다.

```bash
codex mcp add zendesk -- /absolute/path/to/zendesk
codex mcp list
```

다른 MCP client는 보통 같은 의미의 JSON 설정을 사용합니다.

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "/absolute/path/to/zendesk"
    }
  }
}
```

MCP 프로세스는 터미널 환경변수에 의존하지 않고 저장된 연결을 읽습니다. stdio
서버로 실행되는 동안 브라우저를 열지 않습니다.
패키지를 제거해도 Zendesk 접근 권한은 철회되지 않습니다. 연결을 더 이상 쓰지
않으면 Zendesk에서 OAuth grant를 철회하고 위의 정확한 로컬 연결 파일을 제거합니다.

### API token 설정

API token 인증도 유지합니다. MCP 프로세스에 세 환경 변수를 모두 제공합니다.

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

`zendesk check`는 네트워크 요청 없이 구성을 검사하고, `--probe`를 추가하면
비밀값을 출력하지 않고 현재 Zendesk 사용자를 확인합니다.

```bash
zendesk check
```

서버 환경을 위한 기존 confidential OAuth도 유지합니다. 사용하려면
`ZENDESK_SUBDOMAIN`, `ZENDESK_AUTH_MODE=oauth`,
`ZENDESK_OAUTH_CLIENT_ID`, `ZENDESK_OAUTH_CLIENT_SECRET`, 사용자 전용
`ZENDESK_OAUTH_TOKEN_STORE` 경로를 설정합니다. Zendesk에 등록한 redirect URI를
두 명령에서 정확히 동일하게 사용합니다.

```bash
zendesk oauth-start https://your-app.example/callback
zendesk oauth-finish https://your-app.example/callback <state>
```

`oauth-start`는 authorization URL을 출력하고 일회성 state를 저장합니다.
Zendesk가 redirect한 뒤 반환된 `state`를 `oauth-finish`에 넘기면 authorization
code를 화면에 보이지 않게 입력받아 user-only 권한의 token store에 저장합니다.

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
uv sync --group dev
uv run pytest -v
uv build
```

현재 범위는 [foundation 구현 계획](docs/superpowers/plans/2026-09-04-unified-zendesk-mcp-foundation.md),
전체 기능 로드맵은 PRD에서 확인할 수 있습니다.
