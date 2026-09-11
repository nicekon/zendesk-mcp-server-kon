# 구현 완료 감사: 남은 증거와 작업

이 문서는 PRD 14.2의 완료 조건을 대체하지 않는다. 테스트 개수나 개별 수정만으로 전체 완료를 선언하지 않는다.

## 현재 확인한 누락

- Windows: `config._has_unsafe_permissions`는 POSIX 검사를 생략한다. 실제 ACL 보호를 입증하지 못했고 Windows OAuth 저장·갱신 검증도 남아 있다.
- Macro: 현재는 읽기 preview 뒤 ticket PUT 한 번이므로 PRD 7.3의 다중 쓰기 부분 성공 경로는 없다. 적용 여부가 unknown/partial인 오류에는 ticket_id, macro_id, 수동 audit 확인 안내를 추가한다. timeout에서 쓰기 1회와 복구 안내를 검증했다. PRD 14.2의 일반적인 부분 성공 조건 전체가 증명된 것은 아니다.
- Macro preview: [공식 문서](https://developer.zendesk.com/api-reference/ticketing/business-rules/macros/#show-ticket-after-changes)에 따르면 현재 사용하는 ticket별 apply 조회는 변경 필드만이 아닌 전체 티켓을 반환한다. 이 전체 응답을 PUT으로 돌려보내는 현재 구현의 필드 제한·동시 변경 보호를 추가 검토해야 한다.
- 전체 pagination: Community 공통 cursor 처리의 반복·누락 커서와 Search Export의 바로 이전 커서 반복을 거부한다. Search Export 커서의 다른 query 재사용 거부도 검증했다. Support 목록, Guide export와 offset 목록 및 export 전체 상한·streaming 요구사항은 추가 대조가 필요하다.
- 원격 검증: 로컬 테스트 통과는 GitHub CI 성공 증거가 아니다. 원격 CI 결과와 실제 Windows 검증이 필요하다.
- 쓰기 E2E: 운영 계정의 읽기 인증 성공은 disposable sandbox의 승인된 쓰기 검증을 대체하지 않는다.

## 이번에 검증한 경계

- OAuth 콜백: 잘못된 state, Host, path, 중복 code/error, code와 error 혼합, 비ASCII state, 빈 code를 거부한 뒤 정상 콜백으로 완료한다.
- 수동 OAuth: 비ASCII state를 표준 구성 오류로 거부하고 기존 유효 state는 유지한다.
- Search Export 정렬은 [공식 Search 문서](https://developer.zendesk.com/api-reference/ticketing/ticket-management/search/#export-search-results)의 `created_at` 고정 순서를 따른다. 별도 정렬 파라미터를 추가하지 않는다.
- 검증 명령: `uv run pytest -q --tb=short` — 255 passed. 이 결과는 위 미완료 항목을 완료로 바꾸지 않는다.
