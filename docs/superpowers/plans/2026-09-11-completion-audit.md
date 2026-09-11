# 구현 완료 감사: 남은 증거와 작업

이 문서는 PRD 14.2의 완료 조건을 대체하지 않는다. 테스트 개수나 개별 수정만으로 전체 완료를 선언하지 않는다.

## 현재 확인한 누락

- OAuth 검색 권한 충돌(2026-09-11 live): 저장된 OAuth의 `tickets:read` 등 리소스별 scope로 `/api/v2/tickets.json`은 HTTP 200이지만 `/api/v2/search.json`과 `/api/v2/search/export.json`은 HTTP 403이다. 일반 검색 응답은 `You are missing the following required scopes: read`를 명시했다. `type:ticket` 조건으로도 동일하다. 따라서 PRD의 broad `read` 금지와 OAuth 검색 전체 지원은 현재 API에서 동시에 입증되지 않는다. scope는 변경하지 않았으며 검색 성공으로 처리하거나 목록 스캔으로 대체하지 않는다. 공통 요청 클라이언트는 확인된 응답을 `permission_denied`와 `required_scopes: [read]`로 전달하되 임의 upstream 본문은 노출하지 않는다. [공식 scope 설명](https://developer.zendesk.com/api-reference/ticketing/oauth/grant_type_tokens/#scope)은 broad read가 모든 GET 리소스를 허용함을 명시한다. 해결에는 검색용 broad read의 명시적 정책 예외 또는 Zendesk가 지원하는 제한 scope 대안의 추가 확인이 필요하다.
- Windows: `config._has_unsafe_permissions`는 POSIX 검사를 생략한다. 실제 ACL 보호를 입증하지 못했고 Windows OAuth 저장·갱신 검증도 남아 있다.
- Macro: 현재는 읽기 preview 뒤 ticket PUT 한 번이므로 PRD 7.3의 다중 쓰기 부분 성공 경로는 없다. 적용 여부가 unknown/partial인 오류에는 ticket_id, macro_id, 수동 audit 확인 안내를 추가한다. timeout에서 쓰기 1회와 복구 안내를 검증했다. PRD 14.2의 일반적인 부분 성공 조건 전체가 증명된 것은 아니다.
- Macro preview: 전체 응답 재전송을 제거했다. 적용 전 티켓과 비교해 명시된 쓰기 필드의 변경값과 새 comment만 PUT에 포함한다. 조회 시각을 승인 payload에 묶고 `safe_update`/`updated_stamp`로 충돌을 방지한다. 전체 preview에서 읽기 전용·미변경 필드 제외 및 승인 후 timestamp 변경 시 outbound write 0회를 테스트했다. 모든 Macro action이 이 쓰기 필드 집합에 정확히 대응하는지는 별도 API action 대조가 남는다.
- 전체 pagination: Community 공통 cursor 처리의 반복·누락 커서와 Search Export의 바로 이전 커서 반복을 거부한다. Search Export 커서의 다른 query 재사용 거부도 검증했다. Support 목록, Guide export와 offset 목록 및 export 전체 상한·streaming 요구사항은 추가 대조가 필요하다.
- Guide artifact streaming: article 페이지를 사용자 전용 임시 파일에 JSON Lines로 기록하고 최종 JSON/CSV는 행별로 원자적 cache writer에 전달한다. CSV는 임시 파일을 두 번 읽어 뒤쪽 페이지의 열도 보존한다. 이전 페이지 객체가 다음 요청 전에 해제되는지와 중간 upstream 실패 시 artifact를 발행하지 않는지 테스트했다. KB inline 리소스 경로는 여전히 전체 article 목록을 구성하며 CSAT와 ticket export의 전체 streaming 감사도 남는다.
- 원격 검증: 로컬 테스트 통과는 GitHub CI 성공 증거가 아니다. 원격 CI 결과와 실제 Windows 검증이 필요하다.
- 쓰기 E2E: 운영 계정의 읽기 인증 성공은 disposable sandbox의 승인된 쓰기 검증을 대체하지 않는다.

## 이번에 검증한 경계

- KB 리소스: locale별 export의 `truncated`를 응답에 보존하며, 잘못된 articles/truncated 타입은 빈 정상 결과 대신 upstream 오류로 반환한다. 여러 locale 전체에 100,000건 상한을 적용하고 남은 한도를 다음 locale에 전달한다. 생략한 locale이 있으면 전체 `truncated=true`다. 99,999건 뒤 다음 locale에 1건만 요청하고 세 번째 locale을 호출하지 않는 테스트를 통과했다. KB의 전체 메모리 사용은 아직 별도 작업이다.
- Ticket artifact: format 지정 시 기존 Search Export page adapter와 projection을 재사용해 여러 페이지를 spool로 저장한다. 기본 100,000건 상한에서 `truncated=true`와 재개 cursor를 반환한다. 전체 과정의 반복 upstream cursor를 거부하고 페이지 크기·meta 타입을 검증한다. 다중 페이지 파일 내용, 100,000건 중단과 cursor 재개를 로컬 테스트했다. 실제 OAuth Search Export는 위 scope 충돌로 여전히 미검증이다.
- 누적 변경 재검증(2026-09-11): 별도 `UV_PROJECT_ENVIRONMENT`에서 Python 3.10.17, 3.11.16, 3.12.5 각각 `uv run --python VERSION --frozen pytest -q --tb=short` 299 passed. 새 임시 디렉터리로 sdist/wheel을 빌드하고 별도 Python 3.12 환경에 정확한 wheel을 설치한 뒤 저장소 밖 cwd에서 stdio 초기화와 도구 101개를 확인했다. 이는 macOS 로컬 증거이며 Windows, 원격 CI, 운영 쓰기 성공을 증명하지 않는다.
- CSAT artifact: [Legacy](https://developer.zendesk.com/api-reference/ticketing/ticket-management/satisfaction_ratings/)와 [Survey](https://developer.zendesk.com/api-reference/ticketing/ticket-management/csat_survey_responses/)의 cursor 응답으로 여러 페이지를 읽는다. 기존 backend별 filter resolver를 재사용하며 파일 spool과 streaming serializer로 저장한다. 두 backend의 이전 페이지 메모리 해제, 반복 커서 거부, 100,000건 상한과 `truncated`를 검증했다. 실제 계정 CSAT 권한 및 대량 live export는 검증하지 않았다.
- OAuth 콜백: 잘못된 state, Host, path, 중복 code/error, code와 error 혼합, 비ASCII state, 빈 code를 거부한 뒤 정상 콜백으로 완료한다.
- 수동 OAuth: 비ASCII state를 표준 구성 오류로 거부하고 기존 유효 state는 유지한다.
- Search Export 정렬은 [공식 Search 문서](https://developer.zendesk.com/api-reference/ticketing/ticket-management/search/#export-search-results)의 `created_at` 고정 순서를 따른다. 별도 정렬 파라미터를 추가하지 않는다.
- 검증 명령: `uv run pytest -q --tb=short` — 255 passed. 이 결과는 위 미완료 항목을 완료로 바꾸지 않는다.
