# 구현 완료 감사: 남은 증거와 작업

이 문서는 PRD 14.2의 완료 조건을 대체하지 않는다. 테스트 개수나 개별 수정만으로 전체 완료를 선언하지 않는다.

## 현재 확인한 누락

- Windows: `config._has_unsafe_permissions`는 POSIX 검사를 생략한다. 실제 ACL 보호를 입증하지 못했고 Windows OAuth 저장·갱신 검증도 남아 있다.
- Macro 부분 성공: `TicketTools.apply_macro`는 ticket PUT 결과를 반환한다. PRD가 요구하는 `partial_success`와 복구 정보의 실제 발생 조건·처리·테스트를 확인해야 한다. 오류 enum의 존재는 구현 증거가 아니다.
- 전체 pagination: Community 공통 cursor 처리의 반복·누락 커서는 보완했다. Support 목록, Search Export, Guide export와 offset 목록의 요구사항은 별도로 대조해야 한다.
- 원격 검증: 로컬 테스트 통과는 GitHub CI 성공 증거가 아니다. 원격 CI 결과와 실제 Windows 검증이 필요하다.
- 쓰기 E2E: 운영 계정의 읽기 인증 성공은 disposable sandbox의 승인된 쓰기 검증을 대체하지 않는다.

## 이번에 검증한 경계

- OAuth 콜백: 잘못된 state, Host, path, 중복 code/error, code와 error 혼합, 비ASCII state, 빈 code를 거부한 뒤 정상 콜백으로 완료한다.
- 수동 OAuth: 비ASCII state를 표준 구성 오류로 거부하고 기존 유효 state는 유지한다.
- 검증 명령: `uv run pytest -q --tb=short` — 253 passed. 이 결과는 위 미완료 항목을 완료로 바꾸지 않는다.
