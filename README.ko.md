# Zendesk MCP

[English](README.md)

Codex 같은 AI 앱에서 **Zendesk 티켓·도움말·커뮤니티를 대화로 조회**할 수 있게 해주는 연결 도구입니다.

- “최근 접수된 미해결 티켓을 찾아줘.”
- “이 티켓의 대화를 요약하고 답변 초안을 작성해줘.”
- “도움말에서 환불 관련 문서를 찾아줘.”

기본은 **읽기 전용**입니다. 답변 초안을 만들어도 고객에게 자동으로 전송하지 않습니다.
수정·전송은 관리자가 별도로 설정해야 합니다.

## 준비하기

- Zendesk 계정과 MCP를 지원하는 AI 앱이 필요합니다. 아래는 **Codex 기준**입니다.
- [Git](https://git-scm.com/downloads)과 [uv](https://docs.astral.sh/uv/getting-started/installation/)를 설치하세요. uv는 이 도구를 설치하는 프로그램입니다.
- 명령은 AI 채팅창이 아니라 **내 컴퓨터의 터미널**에 입력합니다. macOS에서는 ‘터미널’ 앱을 여세요.
- 관리자에게 **Zendesk 서브도메인과 OAuth 클라이언트 식별자**를 요청하세요.

현재 소스에서 설치하는 버전입니다. 아래 브라우저 로그인은 macOS·Linux용입니다.
**Windows는 [API 토큰 연결](docs/advanced-setup.md#api-token)을 사용하세요.**

## 1. 설치하기

터미널에서 한 줄씩 실행하세요.

```bash
git clone https://github.com/nicekon/zendesk-mcp-server-kon.git
cd zendesk-mcp-server-kon
uv tool install .
zendesk --help
```

사용 안내가 나오면 설치 완료입니다. `zendesk` 명령을 찾을 수 없다고 나오면
`uv tool update-shell`을 실행한 뒤 터미널을 새로 열어 주세요.

## 2. Zendesk에 로그인하기

아래 두 값을 관리자에게 받은 정보로 바꾸세요.

- `your-company`: 주소가 `your-company.zendesk.com`이라면 `your-company`만 입력합니다. 전체 주소나 별도 고객센터 도메인이 아닙니다.
- `your-client-id`: **클라이언트 식별자**입니다. 이메일이나 API 토큰이 아닙니다.

```bash
zendesk login --subdomain your-company --client-id your-client-id
```

브라우저가 열리면 Zendesk에 로그인하고 접근을 허용하세요.
주소창의 코드를 복사하거나 별도 서버를 실행할 필요는 없습니다.
연결 정보는 내 컴퓨터에 저장되므로 매번 로그인하지 않아도 됩니다.

<details>
<summary>관리자라면: 최초 연결 준비</summary>

Zendesk에 **Public 유형의 OAuth 클라이언트**를 만들고, 다음 리디렉션 주소를 정확히 등록하세요.

```text
http://127.0.0.1:3000/oauth/callback
```

사용자에게 서브도메인과 클라이언트 식별자를 전달하세요. 클라이언트 비밀키는 전달하지 않습니다.
기본 로그인은 읽기 권한을 요청합니다. 검색에 필요한 `read` 권한은 검색 외에도 사용자의 역할이 허용하는 데이터 조회에 적용됩니다.
[관리자용 상세 설정](docs/advanced-setup.md)에서 추가 설정을 확인할 수 있습니다.

</details>

## 3. 연결 확인하기

```bash
zendesk check --probe
```

결과에 `"ok": true`, `"configured": true`, `"verified_user"`가 있으면 로그인 확인이 끝났습니다.
오류가 나오면 아래 표를 확인하세요. **토큰이나 비밀번호를 채팅에 붙여 넣지 마세요.**

## 4. Codex에 등록하기

Codex CLI가 설치된 터미널에서 실행 파일의 위치를 확인하세요.

```bash
command -v zendesk
```

출력된 경로를 아래 `/absolute/path/to/zendesk` 대신 넣으세요. 따옴표는 그대로 둡니다.

```bash
codex mcp add zendesk -- "/absolute/path/to/zendesk"
codex mcp list
```

목록에 `zendesk`가 나오면 등록된 것입니다. Codex를 다시 열고 새 대화에서 “Zendesk 연결 상태를 확인해줘”라고 요청해 보세요.
등록 명령을 찾지 못하거나 다른 앱을 사용한다면 [앱별 연결 설정](docs/advanced-setup.md#connect-app)을 참고하세요.

## 문제가 생겼을 때

| 증상 | 확인할 내용 |
| --- | --- |
| 명령을 찾을 수 없음 | `uv tool update-shell` 실행 후 터미널을 다시 여세요. |
| 로그인이 끝나지 않음 | 브라우저의 허용 화면을 확인하세요. 시간이 지났다면 로그인 명령을 다시 실행하세요. |
| `401` 또는 `authentication_failed` | 다시 로그인하세요. API 토큰 방식이면 서브도메인·이메일·토큰을 확인하세요. |
| `403` 또는 `permission_denied` | 관리자에게 해당 기능의 계정 권한을 확인해 달라고 요청하세요. |
| `not_configured` 또는 `unsupported` | 별도 설정이나 Zendesk 요금제가 필요한 기능일 수 있습니다. |
| 조회는 되지만 수정은 안 됨 | 기본 읽기 전용 상태입니다. 오류가 아닙니다. |

일부 기능은 추가 설정이 필요하며 계정·요금제·사용자 권한에 따라 사용 범위가 다릅니다.
**이미지 업로드는 실제 환경의 권한 문제로 검증이 끝나지 않았습니다.**

## 더 알아보기

- [API 토큰·쓰기 권한·선택 기능 설정](docs/advanced-setup.md)
- [기존 버전에서 이전하거나 되돌리기](docs/migration.md)
- [전체 기능과 API 참고](docs/capability-manifest.md)
- [개발·출시 안내](docs/releasing.md)

라이선스: [Apache-2.0](LICENSE)
