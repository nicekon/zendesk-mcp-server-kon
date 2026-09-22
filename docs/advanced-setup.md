# 관리자용 상세 설정 / Advanced setup

일반 설치는 [한글 README](../README.ko.md) 또는 [English README](../README.md)를 먼저 확인하세요.
Start with the README. The settings below are for administrators and alternative connections.

<a id="connect-app"></a>

## AI 앱 연결 / Connect an app

Codex CLI가 없다면 Codex의 사용자 설정 파일 `~/.codex/config.toml`에 아래 항목을 추가할 수 있습니다.
기존 설정을 지우지 말고, 같은 이름의 항목이 있으면 해당 항목만 수정하세요.
Add this entry to your Codex user configuration without replacing unrelated settings.
Replace the example command with the installed executable’s absolute path.

```toml
[mcp_servers.zendesk_mcp]
command = '/absolute/path/to/zendesk'
```

경로 확인 / Find the executable:

- macOS/Linux: `command -v zendesk`
- Windows PowerShell: `where.exe zendesk`

Windows TOML 경로에는 작은따옴표를 사용하세요. 예: `command = 'C:\Users\YOUR_NAME\.local\bin\zendesk.exe'`.
Use the actual path printed on your computer. Reopen the app after saving.
설정 형식은 [Codex 공식 안내](https://developers.openai.com/codex/mcp/)도 참고하세요.

다른 앱에서 JSON 형식을 요구한다면 아래처럼 설정합니다. **Codex 설정 파일은 JSON이 아닌 위 TOML 형식입니다.**
For apps that use JSON MCP settings (not Codex’s TOML file):

```json
{
  "mcpServers": {
    "zendesk_mcp": {
      "command": "/absolute/path/to/zendesk"
    }
  }
}
```

<a id="api-token"></a>

## API 토큰 / API token

Windows 또는 브라우저 로그인을 사용하지 않는 환경에서는 API 토큰을 사용할 수 있습니다.
먼저 README의 1단계까지 설치하세요. 관리자에게 API 토큰 접근 허용과 다음 세 값을 확인하세요.
On Windows, or without browser login, install the tool first and obtain these values from your administrator:

| 설정 / Setting | 입력할 값 / Value |
| --- | --- |
| `ZENDESK_SUBDOMAIN` | `your-company.zendesk.com`의 `your-company` 부분 / subdomain only |
| `ZENDESK_EMAIL` | Zendesk 사용자 이메일 / Zendesk user email |
| `ZENDESK_API_TOKEN` | 관리자가 발급한 API 토큰 / API token from your administrator |

Codex 설정의 해당 서버 아래에 추가하세요. 예시는 실제 값으로 바꿉니다.
Add this under the server entry in Codex’s configuration, replacing the placeholders:

```toml
[mcp_servers.zendesk_mcp.env]
ZENDESK_AUTH_MODE = "api_token"
ZENDESK_SUBDOMAIN = "your-company"
ZENDESK_EMAIL = "agent@example.com"
ZENDESK_API_TOKEN = "YOUR_API_TOKEN"
```

다른 앱에서는 같은 값을 Zendesk MCP 서버의 환경변수(`env`) 설정에 넣으세요.
기본으로 일반 티켓 수정과 Community 글·댓글 작성이 활성화됩니다. Community 작성은 아래의 건별 로컬 승인을 거쳐야 합니다. 앱을 다시 열고 “Zendesk 연결 상태를 확인해줘”라고 요청하세요.
For other apps, set the same server environment variables, reopen the app, and ask it to check the Zendesk connection.
These app settings do not apply to a separate terminal’s `zendesk check` command.

**설정 파일에는 토큰이 저장됩니다. 공유·업로드·Git 커밋하지 말고 본인만 읽을 수 있도록 보호하세요.**
The file contains a secret: restrict access, and never share or commit it. Use `ZENDESK_API_TOKEN`, not the old `ZENDESK_API_KEY` name.
토큰과 비밀번호는 AI 채팅에도 붙여 넣지 마세요.

## 선택 기능 / Optional features

기본은 `support,operations,guide,community`입니다. 추가 기능을 쓸 때는 기존 항목도 함께 지정합니다.
For example, to enable CSAT and Custom Objects as well, add this server environment setting:

```toml
ZENDESK_CAPABILITIES = "support,operations,guide,community,csat,custom_objects"
```

이 줄은 위 `[mcp_servers.zendesk_mcp.env]` 안에 넣으세요. OAuth 연결이라면 API 토큰용 항목은 추가하지 않습니다.
Add it inside the server’s `env` table; do not add API-token credentials to an OAuth setup.
OAuth 사용자는 동일한 기능 목록으로 다시 로그인해야 할 수 있습니다(macOS/Linux 터미널):

```bash
ZENDESK_CAPABILITIES=support,operations,guide,community,csat,custom_objects zendesk login --subdomain your-company --client-id your-client-id
```

로그인 때의 설정은 앱의 기능 설정을 영구적으로 바꾸지 않습니다. **로그인과 앱 실행 양쪽에 동일한 목록**을 지정하세요.
Login settings do not persist the app’s feature selection. Configure both login and the app.
Zendesk에서 구매·활성화되지 않은 기능은 이 설정만으로 사용할 수 없습니다.
Optional features remain subject to Zendesk product availability and permissions.

## 수정·전송 권한 / Write access

새 설치의 기본값은 `ZENDESK_WRITE_MODE=standard`와 `ZENDESK_ENABLE_PUBLIC_WRITES=true`입니다. 따라서 일반 티켓 수정과 Community 글·댓글 작성에 별도 활성화 설정은 필요하지 않습니다.
New installations default to `ZENDESK_WRITE_MODE=standard` and `ZENDESK_ENABLE_PUBLIC_WRITES=true`, so ordinary ticket changes and Community posts/comments need no extra enablement.

권한을 제한하려면 앱의 서버 환경변수에 아래 중 필요한 값을 설정하세요.
Use these server environment variables only to restrict the default policy:

```toml
# 모든 쓰기 차단
ZENDESK_WRITE_MODE = "read_only"

# 일반 티켓 수정은 유지하고 Community 공개 작성만 차단
ZENDESK_ENABLE_PUBLIC_WRITES = "false"
```

기존 읽기 전용 OAuth 토큰에는 `tickets:write` 또는 `hc:write`가 없을 수 있습니다. 실행 중 `oauth_relogin_required`가 나오면, 현재 capability 설정을 유지한 채 `zendesk login`을 다시 실행해 새 범위를 승인하세요. 토큰을 자동으로 변경하거나 범위를 우회하지 않습니다.
Older read-only OAuth tokens may lack `tickets:write` or `hc:write`. If `oauth_relogin_required` appears, run `zendesk login` again with the current capability settings and approve the new scopes. Tokens are never changed automatically or bypassed.

Community 글·댓글 작성은 기본으로 가능하지만, 항상 preview → 아래의 로컬 승인 → apply 절차를 거칩니다. 삭제·대행 작성·외부 업로드는 기본 `false`이며 해당 환경변수를 명시적으로 켜도 일회성 사람 승인이 필요합니다.
Community posts and comments are enabled by default but always follow preview → local approval → apply. Destructive, impersonation, and external-upload gates default to `false`; even when explicitly enabled, they require one-time human approval.
AI가 보여준 작업 미리보기를 확인한 뒤, 사용자 본인이 같은 Zendesk 계정 설정의 터미널에서 실행합니다:

```bash
zendesk approve <approval_request_id>
```

실제 요청 ID로 바꾸고 표시된 작업을 확인한 뒤 승인하세요. AI가 사용자를 대신해 승인하도록 하지 마세요.
Review the exact operation before approving. Approval is single-use and account-bound.
설정 이름은 [환경변수 예시](../.env.example), 권한 변경·복구 절차는 [이전 안내](migration.md)를 참고하세요.

## 연결 관리와 개발 / Connection management and development

- OAuth 연결은 `~/.config/zendesk-mcp-server/connection.json`에 저장됩니다. 패키지를 제거해도 권한이 철회되지는 않습니다. 연결 종료 시 Zendesk에서 접근 권한을 철회하세요. Uninstalling does not revoke access.
- Windows 브라우저 OAuth 로그인은 현재 지원되지 않습니다. API 토큰을 사용하세요. Windows OAuth is currently unsupported.
- 기존 confidential OAuth, 토큰 이전·롤백: [migration.md](migration.md).
- 시간 추적 필드·Git-Zen·캐시 등 관리자 설정: [.env.example](../.env.example).
- 개발: `uv sync --group dev`, `uv run pytest -v`, `uv build`.
- 배포·출시 / Releases: [releasing.md](releasing.md).
