# PRD 14.2 검증 근거 색인

기준 구현: `af83d75bbdb52d20aaffe10ce0797186db0797bc`.
대상은 `../specs/2026-09-04-unified-zendesk-mcp-design.md` 14.2의 36개
완료 조건이며, 아래 번호는 원문 체크리스트 순서다. 원문 범위를 대체하지 않는다.

이 문서는 근거를 찾는 색인이지, 36개 조건 전체의 수용 승인서가 아니다.
`회귀 근거`는 실행 가능한 검증이 있다는 뜻이다. 해당 테스트 밖의 입력·계정·
플랫폼까지 완료됐다는 의미는 아니다. `추가 대조`는 근거 범위가 아직 충분히
확정되지 않은 항목이며, 구현 누락이 확정됐다는 의미도 아니다.

## 조건별 근거

### 최신 설치 검증: f9a6284

- `/tmp/zendesk-final-install.CKQrca`에서 새 sdist/wheel을 빌드하고 정확한 wheel을
  별도 Python 3.12.5 환경에 설치했다. 격리 import가 site-packages를 가리키며
  전체 설치 패키지 테스트 861개가 8.39초에 통과했다.
- 설치된 실행 파일의 stdio initialize와 101개 도구 목록을 확인했다.
  가짜 package-smoke subdomain을 사용했으며 실제 Zendesk 요청은 하지 않았다.
- wheel SHA-256: `7c7fe798492ca66103a302be92c081c45b2fee9162f275ff64b0f1b7b6db5535`.
- 08b1ae0의 잘못된 업로드 응답 단계·요청 ID 보존과 f9a6284의 Badge 오류 단계·
  기존 오류 상세정보 보존 회귀를 포함한다. 좁은 독립 리뷰에서 Critical/Important
  결함은 없었고 Minor 테스트 범위 지적을 후속 보완했다.
- 아래 커밋별 증거는 이력이다. 실제 이미지 업로드 403, Badges 제품/권한 구분,
  전체 sandbox 수용 증거는 해결되지 않았으며 전체 완료율을 산출하지 않는다.

### 사용자 지정 Community 게시물의 실제 검증

사용자가 테스트 게시물 한 건의 본문 수정·원복, 댓글 한 건과 이미지 한 개 업로드를
허용했다. 기존 CLI의 브라우저 재로그인이 완료됐으며 저장된 요청 scope 목록에는
hc:write가 포함됐다. 이것만으로 모든 endpoint의 권한이 증명되지는 않는다.

- 실제 MCP handler에서 preview 후 사용자가 로컬 CLI로 발급한 일회성 토큰으로
  댓글 한 건을 작성했다. notify_subscribers=false로 요청했고 결과는 applied였다.
  최초 보조 목록 확인 스크립트는 목록 envelope를 data.comments로 잘못 읽어 빈
  결과를 표시했다. 재작성 없이 comment ID와 post ID를 지정한 단건 재조회로
  정확한 본문 저장을 확인했다. 이는 서버 목록 기능의 실패 증거가 아니다.
- 본문 수정과 원복의 별도 승인 토큰을 검증하고 원문 일치 여부를 확인한 뒤
  각각 한 번 apply했다. 두 결과 모두 applied였고 각 본문을 재조회했다.
  원래 본문으로 복원됐고 제목은 유지됐다. 테스트 댓글은 남겨 두었다.
- 브랜드의 host_mapping을 확인한 뒤 개인정보 없는 16×16 PNG(79 bytes)를
  external-upload preview에 바인딩했다. 사람 승인 후 apply는 HTTP 403,
  permission_denied 및 실패 요청의 not_applied를 반환했다. 자동 재시도하지 않았다.
  전체 업로드 완료는 미검증이다. 기록된 오류만으로 첫 URL 발급과 마지막 경로
  생성 중 어느 Zendesk POST가 거부됐는지 구별할 수 없으므로 중간 업로드가
  전혀 없었다고 주장하지 않는다. presigned PUT 오류는 별도 메시지를 사용한다.

후속 진단 구현 `4d002ae`는 실패 응답의 error.details.upload_stage에
prepare_upload / upload_binary / create_image_path를 추가한다. 원래 오류 코드,
요청 ID와 요청 단위 operation_state는 보존한다. 세 단계 회귀는 변경 전에
실패했고 전체 855개 테스트 및 CI 34688291018의 다섯 작업이 통과했다.
이는 403 해결이 아니라 실패 단계 식별 기능이다.

사용자가 한 번의 재시도를 별도로 허용하고 새 로컬 승인 토큰을 제공했다.
그 시도는 prepare_upload 단계에서 HTTP 403 / permission_denied / not_applied를
반환했다. 따라서 두 번째 시도에서는 presigned PUT 및 이미지 경로 생성에 도달하지
않았다. 이 증거를 첫 번째 시도의 중간 전송 여부까지 소급해 적용하지 않는다.
추가 업로드, 재로그인, broad write 확대 또는 익명 인증 우회는 하지 않았다.

공식 User Images 문서와 endpoint·payload는 일치하며 익명 사용자도 Allowed for에
명시돼 있다. 따라서 관리자 권한 부족이나 OAuth scope 부족으로 원인을 단정하지
않는다. 재시도 시 단계별 진단이 필요하며 승인 토큰을 재사용하지 않는다.
토큰·이미지 업로드 URL·고객 본문·리소스 식별자는 이 문서에 기록하지 않는다.
다른 게시물, 삭제, Guide/Badge 쓰기 또는 전체 sandbox release 검증의 증거는 아니다.

### 최신 런타임 설치 검증: cb2d16be6cb7911c9f38cd5aa975b1b7013108d3

- `/tmp/zendesk-approved-package.r2xyhP`에 sdist/wheel을 새로 빌드하고 별도
  Python 3.12.5 환경에 정확한 wheel과 pytest를 설치했다.
- `python -I`의 site-packages import 확인 후 `-I -m pytest --import-mode=importlib -q`로
  전체 852개가 8.33초에 통과했다. CLI 로그인 scope 환경 전달 회귀도 포함된다.
- 설치된 실행 파일의 실제 stdio initialize와 101개 도구 목록을 확인했다.
  handshake는 가짜 package-smoke subdomain으로 실행했으며 실제 credential을 쓰지 않았다.
- wheel SHA-256: `b4a63f3a352bae716e1ffd3dc09f3e2c6d3e34c31ba480d6885e52e85c310d91`.
- 아래 이전 커밋의 설치 증거를 보존하되 최신 설치 상태는 이 절을 기준으로 읽는다.
  전체 PRD 수용 및 출시 완료를 뜻하지 않는다.

### 후속 공개 답변의 실제 사람 승인 검증

사용자가 같은 테스트 티켓에 공개 답변 한 건을 별도로 허용했다. cb2d16b의
실제 MCP 요청 처리기로 preview를 생성하고, 사용자가 MCP 밖의 로컬 승인 CLI에서
발급받아 전달한 일회성 토큰으로 동일 payload의 apply를 한 번 실행했다.
apply는 ok=true였고 대화 재조회에서 해당 본문의 댓글이 public=true로 저장된
것을 확인했다. 장기 credential, 승인 토큰, 수신 주소와 티켓 본문은 기록하지 않는다.

이는 공개 답변의 preview→사람 승인→apply→저장 확인 경로에 대한 실제 증거다.
사용자 확인에 따라 메일 수신 여부는 이 MCP 테스트의 완료 조건이 아니다.
Community/Guide/Badge/업로드 등 다른 고위험 작업, 별도 sandbox release check,
제품·역할별 전체 검증을 대체하지 않는다. 추가 운영 쓰기 권한으로 해석하지 않는다.

### 사용자 지정 티켓의 제한된 실제 쓰기 검증

이전의 “실제 쓰기 없음” 기록 이후, 사용자가 직접 생성한 테스트 티켓 한 건과
본인 관리 수신 주소를 지정하고 내부 메모·태그·상태 변경 범위를 허용했다.
브라우저 OAuth 재승인 후 f7636dc의 설치된 MCP 실행 파일로 일반 쓰기 5회를 수행했다.
임시 태그 추가·제거, open→pending→open 및 원래 custom status 복원, 비공개 내부
메모 한 건을 각각 재조회로 확인했다. 다른 티켓·공개 답변·삭제는 실행하지 않았다.

마지막 전체 태그 동등성 assertion은 내부 메모용 Zendesk 트리거가 태그를 추가해
실패했다. 읽기 전용 감사 조회로 rule/trigger 출처를 확인했으며 쓰기를 재전송하지 않았다.
임시 테스트 태그는 제거됐고 트리거의 의미 있는 태그는 보존했다. 원래 상태와 custom
status는 복원됐으며 내부 메모는 남겼다. 테스트 후 기본 read_only도 확인했다.
수신 주소·티켓 본문·credential은 이 문서에 기록하지 않는다.

이는 일반 티켓 쓰기의 제한된 실제 증거다. 위 후속 절의 공개 답변 외 고위험
작업, 수동 sandbox release check, Community/Guide/Badge/업로드 검증을
대체하지 않는다. 해당 티켓의 지정 범위 밖 쓰기를 승인한 것으로 해석하지 않는다.

재인증 준비 중 발견한 CLI 누락도 보완했다. `zendesk login`이 기능·쓰기 설정을
버려 scope를 확장하지 못했던 문제를 3개 실패 회귀로 재현하고 기존 Settings
검증기에 여섯 scope 관련 환경 설정을 전달하도록 수정했다. 전체 로컬 852개 테스트
통과. 이 CLI 변경은 모의 브라우저 경계로 검증했으며 추가 실제 로그인은 하지 않았다.

### 현재 상태 해석: 6f568ccd4ffdc6d1000aeb9566f26746791d0976

아래 표는 최초 작성 시점의 근거와 후속 증거를 함께 보존한다. “추가 대조”는
새 기능을 무한히 추가하거나 모든 가능한 입력 조합을 테스트하라는 조건이 아니다.
명시된 PRD 계약에 실제 누락이 있는지를 기준으로 판단한다.

- 02: G1–G8과 M10 offset 정렬은 후속 구현됐다. 101개 canonical 이름과 원본
  59개 대응 행의 대상 등록을 현재 소스에서 재확인했다. 실제 결과 동등성은 별도다.
- 15: 복합 필터, 객체 ID/lookup 검증, 오류 전파 후속 증거가 있다. 제품 비활성
  판별 누락은 `custom_objects_activated` 감지로 구현했으며 검색·export에 적용했다.
- 20: 본문 read-back, untrusted 표시, 알림 대상, 대행 관리자 검사, 이미지 경로
  검증은 Community 감사의 후속 절 기준으로 읽는다. 최초 누락표는 현재 미구현 목록이 아니다.
- 21·28: CSAT와 Custom Objects의 감지를 연결 상태에 노출한다. 필요한 OAuth
  scope도 표시하지만 실제 토큰 grant나 감지하지 않은 제품의 가용성으로 주장하지 않는다.
- 계속 미검증: 실제 수동 승인 쓰기·업로드, 제품/역할별 실계정 동작, Badges의
  미구매와 권한 부족을 구분할 확정 신호. 테스트 계정 재요청·운영 쓰기·출시는 하지 않는다.

전체 목표는 완료로 표시하지 않는다. 최신 전체 소스 테스트는 849개이며 설치 패키지
검증은 아래에 명시한 별도 커밋 기준이다. 테스트 개수로 수용 범위를 대체하지 않는다.

### 최신 설치 검증: 039497045287bd3f5e6bbd12b413d7c672ac60c9

- `/tmp/zendesk-install-audit.cox3A4`에 sdist 및 wheel을 새로 빌드하고 별도
  Python 3.12.5 환경에 정확한 wheel 파일과 pytest를 설치했다.
- `python -I`로 import가 해당 환경의 `site-packages`에서 이루어짐을 확인했다.
  `-I -m pytest --import-mode=importlib -q`로 전체 823개가 7.15초에 통과했다.
  기본 read-only의 43개 mutation 도구 차단 회귀와 최근 인증 검증도 포함한다.
- 설치된 `zendesk` 실행 파일로 initialize, 101개 도구 목록, 무설정 연결 상태의
  `configured=false`를 확인했다. 사용자 credential 대신 가짜 subdomain을 전달했다.
- wheel SHA-256: `552e60310d4d39e96b747f88ccd17a37c327ecbb3901599681e75faee7ea9e90`.
  같은 소스 커밋의 CI `34678490969`는 completed/success다.
- 실제 Zendesk 쓰기·실제 사람 승인 E2E·제품별 실계정 권한·출시/배포 증거는 아니다.

### 이전 설치 검증: 3f6590981f0eb5a26d9a1cb55e702b4615df8cee

- 새 임시 디렉터리에 sdist와 wheel을 빌드하고 별도 Python 3.12.5 venv에
  정확한 wheel 파일과 pytest를 설치했다. 개발 환경 `.venv`는 변경하지 않았다.
- `python -I` import 경로가 `/private/tmp/zendesk-final-wheel.FunjAc/venv/lib/python3.12/site-packages/`
  아래임을 확인했다. 같은 Python으로 `-I -m pytest --import-mode=importlib -q`를
  실행하여 전체 780 passed (8.42초)를 확인했다. 저장된 연결은 기존 fixture로 격리한다.
- 이 실행에는 43개 mutation 도구의 기본 read-only 차단과 HTTP 전송 경계의
  쓰기 시도 0을 확인하는 `tests/test_readonly_catalog.py`도 포함된다.
- 설치된 `zendesk` 실행 파일로 별도 stdio handshake를 수행했다. initialize,
  도구 101개, list_tickets의 created_at offset sort 및 -status cursor sort 입력을 확인했다.
  실제 사용자 credential을 전달하지 않고 package-smoke 가짜 subdomain만 사용했다.
- wheel SHA-256: `c0b28033870a452ece044cebff85cd38e72ba2d7c0a8e82d2561fd130a32f369`.
  해당 커밋의 원격 CI `34677436164`도 completed/success로 확인했다.
- 이것은 패키지 설치·모의 동작 검증이다. 실제 계정 쓰기·사람 승인 E2E·플랜별 권한·
  출시/배포를 증명하지 않으며 36개 수용 조건 전체를 완료로 바꾸지 않는다.

tests/docs/.github 경로는 저장소 루트 기준이며, `auth.py` 같은 소스 파일 약칭은
`src/zendesk_mcp_server/` 기준이다. 테스트 이름은 pytest의 `파일::함수`로 실행한다.
작성 시 36개 조건의 순서와 58개 테스트 함수 참조가 실제 존재함을 확인했고,
그 함수들을 실행한 83개 parameterized case가 통과했다. 이것은 참조된 assertion의
실행 결과이며, 표의 추가 대조 항목을 자동으로 해소하지 않는다.

| 번호 | 원문 조건 요약 | 현재 근거 | 판단과 남은 범위 |
| --- | --- | --- | --- |
| 01 | Canonical 등록·exact contract | `tests/test_server.py::test_support_read_tools_are_registered`, `tests/test_server.py::test_mcp_catalog_matches_frozen_input_output_and_risk_contracts` | 101개 이름·입출력 schema·annotation 회귀 근거. 이름은 PRD 8장과 대조했고 schema fixture는 현재 계약 기준이다. |
| 02 | 세 구현의 승인된 기능 합집합 | [원본 기능 대응표](2026-09-12-feature-union-map.md), PRD 2.1·7·8, `server.py`, `tools/` | 9/29/21개 표면 및 프롬프트/KB를 연결했고 G1–G8은 대응표의 후속 절에서 구현·로컬 검증했다. M06/M19는 PRD의 링크 추출·설정 필드 시간 추적 계약으로 대조했고 M10 cursor 상태 정렬도 보완했다. 원본 offset 정렬 입력과의 동등성 및 전체 합집합 수용 판단은 아직 별도다. |
| 03 | 두 인증 방식의 독립 인증·조회 | 사용자가 제공한 API-token/OAuth probe 성공 결과, 누적 감사의 OAuth 검색·metadata 읽기 기록 | 실제 성공 기록은 있지만 이 색인 작성 시 두 인증을 새로 실행하지 않았다. 읽기 증거이며 쓰기 증거가 아니다. |
| 04 | auto 선택·실패 후 no-fallback | `tests/test_config.py::test_auto_uses_api_token_when_no_oauth_settings_exist`, `tests/test_config.py::test_auto_prefers_complete_oauth_without_api_token`, `tests/test_server.py::test_oauth_authorization_failure_requires_reauthorization` | 회귀 근거. |
| 05 | 불완전 OAuth 구성 거부 | `tests/test_config.py::test_auto_rejects_partial_oauth_without_token_fallback` | API-token이 함께 있어도 구성 오류를 요구한다. |
| 06 | state·refresh·동시 회전·원자 저장 | `tests/test_auth.py`, `tests/test_login.py`, `tests/test_locking.py`; `auth.py`, `locking.py` | 상태 단일 사용·권한·회전 재사용 회귀 근거. Windows private storage는 지원 불가 시 중단하며 지원 성공으로 간주하지 않는다. |
| 07 | capability scope·승인된 Support read 예외 | `tests/test_config.py::test_oauth_scopes_are_limited_to_enabled_capabilities_and_gates`, `tests/test_auth.py::test_support_search_read_exception_is_in_authorization_url_without_enabling_writes` | 회귀 근거. 예외 read는 검색 외 역할 허용 GET도 포함하며 global write 예외는 없다. |
| 08 | 목록 envelope·여러 페이지·빈 페이지·cursor·외부 next | `tests/test_pagination.py`, `tests/test_community.py::test_community_empty_final_page_clears_continuation`, 각 도메인 pagination 테스트 | 공통 수집기와 도메인 회귀 근거. 전체 endpoint의 실제 다중 페이지 검증과 구별한다. |
| 09 | 읽기 retry 제한·쓰기 no-replay | `tests/test_client.py::test_read_retries_a_rate_limit_at_most_twice`, `tests/test_client.py::test_read_rate_limit_retries_never_sleep_more_than_thirty_seconds_total`, `tests/test_client.py::test_write_401_refreshes_without_replaying` | 통제된 transport·sleep 회귀 근거. |
| 10 | HTTP/error·operation state | `tests/test_client.py::test_client_maps_known_http_errors`, `tests/test_client.py::test_rejected_writes_report_known_state_without_retry`, `tests/test_client.py::test_successful_transport_reports_applied_only_for_writes` | 표준 오류 및 성공 쓰기 applied 회귀 근거. |
| 11 | 새 설치 read-only outbound write 0 | `tests/test_readonly_catalog.py::test_all_write_tools_block_apply_with_default_readonly_settings`, 각 도메인 write-gate 테스트; `.github/workflows/ci.yml` installed-wheel gate | 새 wheel 별도 venv의 site-packages import를 확인하고 43개 mutation 도구를 실제 handler/domain/client로 호출: 전부 write_disabled, HTTP 전송 경계의 쓰기 시도 0. 읽기 preflight는 모의 응답이며 실제 계정/모든 입력 조합 검증은 아니다. Linux CI에 동일 검사 추가. |
| 12 | 고위험 gate·사람 승인·TTL·single-use·payload 결합 | `tests/test_approvals.py`, `tests/test_write_policy.py`, Guide/Community/Ticket의 승인 테스트; `approvals.py` | 로컬 저장·gate 회귀 근거. 실제 사람+Zendesk 쓰기 검증은 13번에 남긴다. |
| 13 | production 우회 없음·수동 sandbox 승인 쓰기 | `__init__.py`의 대화형 approve 경로, `tests/test_approvals.py::test_approve_cli_displays_the_preview_before_issuing_a_token` | 수동 외부 검증 보류. 테스트 계정이 없으며 재요청하지 않는다. 로컬 테스트 발급을 실제 사람 승인 E2E로 부르지 않는다. |
| 14 | 공개 답변·내부 메모 분리·내부 기본값 | `tests/test_tickets.py::test_create_ticket_uses_a_validated_standard_write_payload`, `tests/test_tickets.py::test_internal_note_is_a_standard_write_without_public_approval` | 회귀 근거. 생성 comment public=false 및 전용 공개 답변 gate. |
| 15 | TicketQuery·resolver·projection의 세 소비자 일관성 | `tests/test_tickets.py::test_structured_ticket_filter_uses_the_same_serializer_for_search_count_and_export`, `test_user_reference_variants_reach_all_three_ticket_consumers`, `test_entity_name_and_id_references_match_across_ticket_consumers` (같은 파일), `tests/test_server.py::test_custom_object_mcp_search_and_csv_artifact_use_real_factories` | assignee/requester의 6종 참조 × 3개 소비자 36조합, organization/brand/group/form의 name/id × 3개 소비자 24조합을 실제 resolver와 serializer로 검증했다. 잘못된 후보 ID는 유일성 판정 전에 upstream_error로 차단한다. 각 필터 단독의 합성 응답 근거이며 여러 필터 동시 조합·projection 전체 계약의 추가 대조는 남는다. |
| 16 | 검색 1000·전용 export·정렬·query-bound TTL | `tests/test_tickets.py::test_search_non_divisor_limit_stays_inside_thousand_result_window`, `tests/test_tickets.py::test_ticket_export_uses_dedicated_export_type_filter`, `tests/test_tickets.py::test_ticket_export_rejects_an_expired_resume_cursor` | 회귀 근거. 실제 대량 export 전체와 구별한다. |
| 17 | 첨부 소속·host/path/size·stream·검사 제한 | `tests/test_tickets.py::test_attachment_download_revalidates_ownership_and_uses_fixed_cache`, `tests/test_client.py::test_attachment_download_streams_into_cache`, text/PDF/image/archive 검사 테스트 | 회귀 근거. Linux PDF subprocess와 플랫폼별 unsupported 경계를 유지한다. |
| 18 | 삭제·위험·미검사 첨부 차단·override 불변 | `tests/test_tickets.py::test_attachment_download_requires_safe_scan_and_size`, `tests/test_tickets.py::test_attachment_download_rejects_unscanned_content_before_downloading` | 회귀 근거. 실제 malware 자료 다운로드를 검증 수단으로 쓰지 않는다. |
| 19 | CDN/presigned 인증 유출·symlink/TOCTOU | `tests/test_client.py::test_attachment_download_removes_authorization_before_cdn_redirect`, `tests/test_client.py::test_presigned_upload_never_sends_zendesk_authorization`, `tests/test_community.py::test_image_upload_rejects_parent_symlink_swap_before_open` | 통제된 HTTP·파일 경계 회귀 근거. |
| 20 | Community 전체 공식 계약 | `docs/capability-manifest.md`, `tests/test_community.py`, `tools/community.py`; 댓글 투표: `tests/test_community.py::test_comment_votes_use_the_official_endpoint_and_resume`, `tests/test_server.py::test_comment_vote_mcp_dispatch_preserves_comment_scope` | 7.5의 댓글별 투표 목록 누락을 공식 Votes endpoint와 대조해 보완했다. post_id+comment_id 범위, cursor 재개, 잘못된 범위 무요청, 실제 MCP 전달의 합성 응답 회귀 근거다. 나머지 리소스별 권한·HTML·알림·pagination 세부 대조는 남으며 전체 suite 성공만으로 이 복합 조건을 닫지 않는다. |
| 21 | 조건부 기능 unsupported/not_configured | `tests/test_server.py::test_disabled_capability_returns_before_building_its_tool_client`, `tests/test_tickets.py::test_custom_object_projection_requires_its_capability`, 조건부 설정 테스트 | 로컬 capability 차단 회귀 근거. 실제 계정별 제품/플랜별 응답은 제한적으로만 확인했다. |
| 22 | Guide 브랜드·locale·ID wire type | `tests/test_guide.py::test_article_publish_binds_brand_to_approval_and_uses_its_subdomain`, `tests/test_guide.py::test_article_translation_accepts_a_string_help_center_id`, `tests/test_guide.py::test_navigation_locale_uses_selected_brand_and_preserves_cursor`, locale 테스트 | category·section의 locale 입력 누락을 후속 보완했다. 브랜드별 활성 locale·고정 주소·cursor·문자열 ID 회귀 근거이며 실제 모든 브랜드 조회 증거는 아니다. |
| 23 | body upsert 거부·별도 파괴적 교체·locale publish | `tests/test_guide.py::test_translation_upsert_refuses_to_replace_an_existing_body`, `tests/test_guide.py::test_translation_body_replacement_is_destructive_and_separate`, `tests/test_guide.py::test_publish_translation_requires_public_approval_and_reads_back` | 회귀 근거. 실제 content block 손실 여부를 테스트 계정 없이 실험하지 않는다. |
| 24 | draft=false create/upsert 거부·공개 title·draft 전환 gate | `tests/test_guide.py::test_article_create_rejects_publishing_before_any_request`, `tests/test_guide.py::test_upsert_of_published_translation_requires_public_and_destructive_gates` | 회귀 근거. |
| 25 | 이미지·badge upload 승인 전 outbound 0 | `tests/test_community.py::test_user_image_upload_requires_approved_external_upload_and_safe_local_file`, `tests/test_community.py::test_badge_icon_upload_uses_the_secure_external_upload_flow` | 회귀 근거. 실제 presigned 업로드 E2E는 보류. |
| 26 | 혼합 user votes·scan resume·구독 upsert/read-back | `tests/test_community.py::test_community_user_vote_cursor_resumes_an_unconsumed_mixed_page`, `tests/test_community.py::test_user_subscription_upsert_previews_change_and_reads_back_after_approval`, `tests/test_community.py::test_subscription_write_with_unverified_readback_is_unknown_without_retry` | 회귀 근거. include_comments가 없는 실응답을 임의로 검증 성공 처리하지 않는다. |
| 27 | CSAT backend 필터·epoch 단위 | `tests/test_guide.py::test_csat_adapters_only_send_their_official_filters`, `tests/test_guide.py::test_survey_list_and_export_use_official_suffixless_path`, `tests/test_guide.py::test_csat_auto_uses_account_features_once_per_operation`, `tests/test_guide.py::test_csat_auto_does_not_guess_or_fallback_when_detection_fails` | score 기반 추정을 Account Settings의 활성 feature 감지로 보완했다. 목록·export 다중 페이지와 불명확 정보·권한 실패 no-fallback 회귀 근거. 실제 두 계정 유형의 감지 E2E를 검증한 것은 아니다. |
| 28 | 비밀 없는 status/locales·무설정 무네트워크 handshake | `tests/test_server.py::test_connection_status_does_not_expose_credentials`, `tests/test_handshake.py::test_unconfigured_server_completes_an_mcp_handshake_without_network_access` | mock status 및 socket 연결 금지 상태의 실제 stdio handshake 근거. |
| 29 | Macro partial_success·복구 정보 | `tests/test_tickets.py::test_macro_unknown_write_outcome_includes_recovery_without_replay`, `test_ticket_macro_preview_requires_approval_and_reuses_ticket_update` (같은 파일); `TicketTools.apply_macro` | 단일 PUT에 필드·댓글을 함께 보내며, timeout/unknown과 명시적 partial_success/partial 오류 각각을 성공으로 바꾸지 않고 복구 ID·안내와 함께 반환하는 합성 회귀를 검증했다. 실제 transport가 부분 적용을 확정하는 근거는 없으며 단일 요청 타임아웃을 partial로 추정하지 않는다. 아래 7.3 대조 참고. |
| 30 | Macro 동적 위험 승격 | `tests/test_tickets.py::test_ticket_macro_raises_all_gates_from_its_actions`, `tests/test_tickets.py::test_macro_risks_include_effective_preview_impersonation_and_closure` | 회귀 근거. |
| 31 | Python 3.10–3.12 test→wheel→MCP CI | `.github/workflows/ci.yml`, CI `34671700267` | 해당 구현 커밋에서 Linux 3버전 전체 순서 성공. macOS/Windows는 별도 package smoke만 성공. |
| 32 | 로그에 token·Authorization·고객 본문 없음 | `tests/test_server.py::test_tool_call_writes_a_redacted_audit_event`, `tests/test_client.py::test_scope_denial_reports_broad_read_requirement_without_leaking_body`, `audit.py` | 허용 필드 기반 로그 및 오류 회귀 근거. 임의 입력 전체의 비밀 탐지/DLP 보장이 아니다. |
| 33 | ResourceLink·ImageContent·경로/base64 제한 | `tests/test_server.py::test_attachment_download_result_includes_a_resource_link`, `tests/test_server.py::test_attachment_image_result_uses_image_content`, export resource 테스트 | MCP 경계 회귀 근거. |
| 34 | 설치·인증·scope·gate·migration·복구 문서 | `README.md`, `README.ko.md`, `.env.example`, `docs/migration.md` | 파일과 내용 존재. 설치 안내 후속 수정 및 관련 CI는 누적 감사 참조. |
| 35 | OAuth rollback 최신 회전 credential·오래된 backup 금지 | `docs/migration.md`의 OAuth store migration/Package rollback, `tests/test_auth.py::test_refresh_preserves_saved_connection_identity` | 운영 절차 문서와 회전 저장 회귀는 별개 근거. 실제 rollback 훈련 완료를 주장하지 않는다. |
| 36 | 운영 계정 무승인 쓰기 E2E 금지 | 현재 작업은 MockTransport/합성 파일/로컬 승인 테스트; 실제 작업 이력은 누적 감사 | 계속 유지할 작업 경계. 운영 계정 쓰기를 검증 대체 수단으로 사용하지 않는다. |

## 다음 확인 순서

15번 복합 필터 후속: `tests/test_tickets.py::test_combined_ticket_filters_preserve_resolved_conditions_and_reject_ambiguity`는
검색·개수·내보내기에 text/status/priority/type/attachment/date/custom field/tag와
assignee/requester/organization/brand/group/form 참조를 한 번에 전달한다.
서로 다른 사용자 ID 및 모든 필터의 최종 query 보존, export의 filter[type] 분리,
입력 객체 불변을 검증한다. 마지막 form 이름이 모호하면 후보 ID를 반환하고 티켓
검색 요청에 도달하지 않는 경우까지 6개 합성 사례가 통과했다. 기존 구현 변경은 없으며,
이 대표 복합 조건의 근거가 모든 필터 조합이나 projection 전체 계약을 증명하지는 않는다.

15번 projection 후속: `_project_custom_objects`가 잘못된 ticket field ID를 조용히 제외해
불완전한 연결 정보를 성공으로 반환할 수 있어, 공통 경로에서 upstream_error로 차단했다.
`tests/test_tickets.py::test_custom_object_projection_rejects_malformed_lookup_metadata`는
검색·내보내기 각각의 null/boolean/0/string ID 응답을 검증한다. 전체 734 passed.
정상 field 선택·객체 확장·CSV 평탄화는 기존 회귀와 함께 통과했으며 실계정 증거는 아니다.

15번 객체 응답 식별 후속: 공식 Custom Object Records의 문자열 id가 요청 ID와 같아야 하며,
custom_object_key가 제공되면 요청한 객체 key와 같아야 한다. 기존 dict 형태 검증만으로는
빈 객체나 다른 레코드가 통과했다. `test_custom_object_projection_rejects_wrong_record_identity`
(tests/test_tickets.py)는 검색·내보내기의 잘못된 5종 응답을 검증한다.
정상 문자열·정수 lookup 참조가 문자열 응답 ID와 일치하는 경우도 보존한다. 전체 740 passed.
근거: [Custom Object Records](https://developer.zendesk.com/api-reference/custom-data/custom-objects/custom_object_records/).

29번과 PRD 7.3 대조: 7.3은 가능한 단일 update를 요구하고 여러 호출이 필요한 경우에
단계별 partial 결과를 요구한다. [원본 michaelrice macros](https://github.com/michaelrice/zendesk-mcp/blob/8313e117094d005dcf1fc48ffb6f9197bc60a712/src/zendesk_mcp/tools/macros.py)는
필드·댓글을 두 번 갱신하지만, 현재 구현은 단일 PUT이며 분할 쓰기 fallback이 없다.
[공식 Macros](https://developer.zendesk.com/api-reference/ticketing/business-rules/macros/)의
preview는 쓰기가 아니고 result.ticket.comment를 포함한다. 따라서 partial 테스트를 위해
실제 쓰기를 인위적으로 분할하거나 타임아웃을 부분 적용으로 단정하지 않는다.
명시적 partial 오류 처리의 로컬 회귀는 추가했지만, 체크리스트의 실계정 부분 적용 증거와는
구분한다. 이 대조로 전체 수용 체크박스를 자동으로 닫지 않는다.

Community 콘텐츠 추가 대조 결과: [콘텐츠 계약 감사](2026-09-12-community-content-audit.md).
Post/Comment HTML 저장 후 read-back 누락은 공통 쓰기 경로와 21개 합성 회귀 사례로 보완했다.
Community 읽기 콘텐츠 표시·분리는 공통 읽기 경로에 보완했고 목록·단건·저장 후 조회·MCP
전달로 검증했다. 이 두 누락의 보완만으로 20번 전체를 완료로 처리하지 않는다.

1. 02: G1–G8 및 M10 offset 정렬을 미구현으로 재작업하지 않는다. 대응표의 후속
   구현·이전 계약 근거를 사용하고 전체 합집합의 수용 범위를 대조한다.
2. 15·20·29: 복합 요구의 하위 항목별 근거를 대조한다. 특히 Macro의 단일 PUT
   구현에서 확인 가능한 부분 성공과 단순 결과 불명을 구분한다. 재현되는 구현/테스트 누락만
   수정하며 기존 기능을 넓히거나 범위를 축소해 통과시키지 않는다.
3. 11: 설치 wheel의 read-only 보안 검증 연결은 구현되어 있다. 최신 배포 후보를
   확정할 때 동일 CI와 설치 검사를 다시 실행한다.
4. 13 등 실제 쓰기/수동 운영 검증은 보류로 유지한다. 계정 재요청·운영 쓰기·병합·
   출시·배포를 자동으로 수행하지 않는다. 이 보류를 코드 검토의 차단 사유로 삼지 않는다.

이 색인으로 PRD 체크박스를 일괄 완료 처리하지 않는다. 회귀 근거의 실제 assertion과
요구 범위를 대조한 뒤에만 개별 수용 판단을 갱신한다.

### 인증 사용자 ID 검증 후속 보완

연결 상태 확인은 빈 사용자 객체도 `verified_user`로 표시했고, OAuth 로그인은 참값인
문자열·boolean·음수·소수 ID도 허용했다. 두 경로가 공통 인증 검사를 사용해 양의 정수
ID만 허용하도록 보완했다. 모의 응답 회귀에서 연결 확인의 `upstream_error`와 로그인
실패 시 기존 연결 파일의 바이트 단위 보존을 확인했다. 전체 로컬 테스트는 822개 통과했다.
이는 사용자 ID 형식 검증 증거이며 역할·OAuth scope·조건부 제품의 전체 탐지 증거는 아니다.
운영 Zendesk 요청이나 실제 로그인 변경은 수행하지 않았다.

### 연결 상태 OAuth scope 표시

PRD 8.1 대조에서 누락된 OAuth scope 정보를 `oauth_required_scopes`로 추가했다.
현재 capability·write gate에 필요한 기존 scope 계산 결과를 재사용하며 토큰에 실제
부여된 권한으로 표시하지 않는다. 모의 설정에서 기본 Community와 대행 gate의 서로
다른 scope 목록, 비밀정보 비노출, probe 없는 상태 확인의 인증 I/O 부재를 검증했다.
전체 로컬 테스트 823개 통과. 이 필드만으로 실제 grant·조건부 제품 탐지 수용을 닫지 않는다.

### 연결 상태 CSAT 감지 결과

PRD 8.1의 감지된 조건부 capability 표시를 기존 CSAT detector와 연결했다.
CSAT 활성 설정의 identity probe가 성공하면 동일 client로 Account Settings를 읽고
`capability_detection.csat`에 backend 또는 표준 감지 오류를 반환한다. legacy/survey,
비활성, 불완전한 feature 응답, permission_denied의 다섯 모의 응답과 GET 순서를
검증했으며 전체 로컬 824개 테스트가 통과했다. `capabilities`는 계속 설정값이고,
감지 결과에 없는 badges 등의 제품을 사용 가능·불가능으로 추정하지 않는다.
실제 제품별 계정 검증 및 나머지 조건부 제품 탐지 완료 증거는 아니다.

### Custom object projection 오류 전파 대조

`test_custom_object_projection_preserves_permission_and_missing_resource_errors`
(tests/test_tickets.py)는 검색·내보내기 각각에서 metadata/record 권한 거부,
record not_found, 연결 lookup 부재를 대조한다. 권한 및 단건 미발견 오류는 request_id를
포함해 보존하고, lookup 부재는 validation_error로 반환하며 부분 결과를 성공으로
노출하지 않는 8개 회귀 사례가 통과했다. 전체 로컬 832개 테스트 통과. 기존 공통 경로가
처리하므로 production 코드는 변경하지 않았다. 단건 404로 제품 미지원을 추정하거나,
이 테스트를 실제 계정의 제품 부재 감지 증거로 해석하지 않는다.

### Custom Objects 계정 활성 감지 구현

공식 [Account Settings Active Features](https://developer.zendesk.com/api-reference/ticketing/account-configuration/account_settings/#active-features)의
`custom_objects_activated` boolean으로 제품 비활성과 lookup 부재를 구분한다.
`TicketTools.detect_custom_objects`를 projection 및 연결 상태에서 재사용하며 false는
unsupported, 누락/형식 오류는 upstream_error, HTTP 오류는 원본으로 반환한다.
기존 정상 projection 회귀에 명시적인 활성 계정 응답을 추가했다. 검색·페이지 export·
JSON 파일 export의 비활성/불명확 응답, 파일 미생성, account/field/record 권한 오류와
404 보존, 연결 상태의 활성/오류 표시를 포함해 전체 로컬 849개 테스트가 통과했다.
계정 정보는 모의 응답이며 운영 Zendesk 요청이나 제품 활성화 변경은 수행하지 않았다.
