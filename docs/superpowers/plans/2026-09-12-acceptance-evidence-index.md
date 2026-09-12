# PRD 14.2 검증 근거 색인

기준 구현: `af83d75bbdb52d20aaffe10ce0797186db0797bc`.
대상은 `../specs/2026-09-04-unified-zendesk-mcp-design.md` 14.2의 36개
완료 조건이며, 아래 번호는 원문 체크리스트 순서다. 원문 범위를 대체하지 않는다.

이 문서는 근거를 찾는 색인이지, 36개 조건 전체의 수용 승인서가 아니다.
`회귀 근거`는 실행 가능한 검증이 있다는 뜻이다. 해당 테스트 밖의 입력·계정·
플랫폼까지 완료됐다는 의미는 아니다. `추가 대조`는 근거 범위가 아직 충분히
확정되지 않은 항목이며, 구현 누락이 확정됐다는 의미도 아니다.

## 조건별 근거

tests/docs/.github 경로는 저장소 루트 기준이며, `auth.py` 같은 소스 파일 약칭은
`src/zendesk_mcp_server/` 기준이다. 테스트 이름은 pytest의 `파일::함수`로 실행한다.
작성 시 36개 조건의 순서와 58개 테스트 함수 참조가 실제 존재함을 확인했고,
그 함수들을 실행한 83개 parameterized case가 통과했다. 이것은 참조된 assertion의
실행 결과이며, 표의 추가 대조 항목을 자동으로 해소하지 않는다.

| 번호 | 원문 조건 요약 | 현재 근거 | 판단과 남은 범위 |
| --- | --- | --- | --- |
| 01 | Canonical 등록·exact contract | `tests/test_server.py::test_support_read_tools_are_registered`, `tests/test_server.py::test_mcp_catalog_matches_frozen_input_output_and_risk_contracts` | 101개 이름·입출력 schema·annotation 회귀 근거. 이름은 PRD 8장과 대조했고 schema fixture는 현재 계약 기준이다. |
| 02 | 세 구현의 승인된 기능 합집합 | PRD 2.1·7·8, `server.py`, `tools/` | 추가 대조: 원본 9/29/21개 표면과 승인된 보안 예외를 항목별로 연결한 최종 대응표는 아직 없다. 도구 수만으로 충족 판정하지 않는다. |
| 03 | 두 인증 방식의 독립 인증·조회 | 사용자가 제공한 API-token/OAuth probe 성공 결과, 누적 감사의 OAuth 검색·metadata 읽기 기록 | 실제 성공 기록은 있지만 이 색인 작성 시 두 인증을 새로 실행하지 않았다. 읽기 증거이며 쓰기 증거가 아니다. |
| 04 | auto 선택·실패 후 no-fallback | `tests/test_config.py::test_auto_uses_api_token_when_no_oauth_settings_exist`, `tests/test_config.py::test_auto_prefers_complete_oauth_without_api_token`, `tests/test_server.py::test_oauth_authorization_failure_requires_reauthorization` | 회귀 근거. |
| 05 | 불완전 OAuth 구성 거부 | `tests/test_config.py::test_auto_rejects_partial_oauth_without_token_fallback` | API-token이 함께 있어도 구성 오류를 요구한다. |
| 06 | state·refresh·동시 회전·원자 저장 | `tests/test_auth.py`, `tests/test_login.py`, `tests/test_locking.py`; `auth.py`, `locking.py` | 상태 단일 사용·권한·회전 재사용 회귀 근거. Windows private storage는 지원 불가 시 중단하며 지원 성공으로 간주하지 않는다. |
| 07 | capability scope·승인된 Support read 예외 | `tests/test_config.py::test_oauth_scopes_are_limited_to_enabled_capabilities_and_gates`, `tests/test_auth.py::test_support_search_read_exception_is_in_authorization_url_without_enabling_writes` | 회귀 근거. 예외 read는 검색 외 역할 허용 GET도 포함하며 global write 예외는 없다. |
| 08 | 목록 envelope·여러 페이지·빈 페이지·cursor·외부 next | `tests/test_pagination.py`, `tests/test_community.py::test_community_empty_final_page_clears_continuation`, 각 도메인 pagination 테스트 | 공통 수집기와 도메인 회귀 근거. 전체 endpoint의 실제 다중 페이지 검증과 구별한다. |
| 09 | 읽기 retry 제한·쓰기 no-replay | `tests/test_client.py::test_read_retries_a_rate_limit_at_most_twice`, `tests/test_client.py::test_read_rate_limit_retries_never_sleep_more_than_thirty_seconds_total`, `tests/test_client.py::test_write_401_refreshes_without_replaying` | 통제된 transport·sleep 회귀 근거. |
| 10 | HTTP/error·operation state | `tests/test_client.py::test_client_maps_known_http_errors`, `tests/test_client.py::test_rejected_writes_report_known_state_without_retry`, `tests/test_client.py::test_successful_transport_reports_applied_only_for_writes` | 표준 오류 및 성공 쓰기 applied 회귀 근거. |
| 11 | 새 설치 read-only outbound write 0 | `tests/test_tickets.py::test_create_ticket_requires_write_mode_before_a_client_is_used`, `tests/test_server.py::test_canonical_names_preserve_dispatch_capabilities_and_approval_identity`, 각 도메인 write-gate 테스트; CI wheel 설치 | 계층별 회귀·설치 근거. 추가 대조: fresh wheel의 모든 쓰기 도구를 한 번에 호출한 write-zero 증거와 동일시하지 않는다. |
| 12 | 고위험 gate·사람 승인·TTL·single-use·payload 결합 | `tests/test_approvals.py`, `tests/test_write_policy.py`, Guide/Community/Ticket의 승인 테스트; `approvals.py` | 로컬 저장·gate 회귀 근거. 실제 사람+Zendesk 쓰기 검증은 13번에 남긴다. |
| 13 | production 우회 없음·수동 sandbox 승인 쓰기 | `__init__.py`의 대화형 approve 경로, `tests/test_approvals.py::test_approve_cli_displays_the_preview_before_issuing_a_token` | 수동 외부 검증 보류. 테스트 계정이 없으며 재요청하지 않는다. 로컬 테스트 발급을 실제 사람 승인 E2E로 부르지 않는다. |
| 14 | 공개 답변·내부 메모 분리·내부 기본값 | `tests/test_tickets.py::test_create_ticket_uses_a_validated_standard_write_payload`, `tests/test_tickets.py::test_internal_note_is_a_standard_write_without_public_approval` | 회귀 근거. 생성 comment public=false 및 전용 공개 답변 gate. |
| 15 | TicketQuery·resolver·projection의 세 소비자 일관성 | `tests/test_tickets.py::test_structured_ticket_filter_uses_the_same_serializer_for_search_count_and_export`, resolver·custom object 테스트, `tests/test_server.py::test_custom_object_mcp_search_and_csv_artifact_use_real_factories` | 회귀 근거. 추가 대조: me/none/id/name/email/phone 및 모든 entity 조합의 개별 근거를 상세 연결해야 한다. |
| 16 | 검색 1000·전용 export·정렬·query-bound TTL | `tests/test_tickets.py::test_search_non_divisor_limit_stays_inside_thousand_result_window`, `tests/test_tickets.py::test_ticket_export_uses_dedicated_export_type_filter`, `tests/test_tickets.py::test_ticket_export_rejects_an_expired_resume_cursor` | 회귀 근거. 실제 대량 export 전체와 구별한다. |
| 17 | 첨부 소속·host/path/size·stream·검사 제한 | `tests/test_tickets.py::test_attachment_download_revalidates_ownership_and_uses_fixed_cache`, `tests/test_client.py::test_attachment_download_streams_into_cache`, text/PDF/image/archive 검사 테스트 | 회귀 근거. Linux PDF subprocess와 플랫폼별 unsupported 경계를 유지한다. |
| 18 | 삭제·위험·미검사 첨부 차단·override 불변 | `tests/test_tickets.py::test_attachment_download_requires_safe_scan_and_size`, `tests/test_tickets.py::test_attachment_download_rejects_unscanned_content_before_downloading` | 회귀 근거. 실제 malware 자료 다운로드를 검증 수단으로 쓰지 않는다. |
| 19 | CDN/presigned 인증 유출·symlink/TOCTOU | `tests/test_client.py::test_attachment_download_removes_authorization_before_cdn_redirect`, `tests/test_client.py::test_presigned_upload_never_sends_zendesk_authorization`, `tests/test_community.py::test_image_upload_rejects_parent_symlink_swap_before_open` | 통제된 HTTP·파일 경계 회귀 근거. |
| 20 | Community 전체 공식 계약 | `docs/capability-manifest.md`, `tests/test_community.py`, `tools/community.py` | 추가 대조: 리소스별 공식 문서·권한·HTML·알림·pagination에 대한 세부 대응 확인이 필요하다. 전체 suite 성공만으로 이 복합 조건을 닫지 않는다. |
| 21 | 조건부 기능 unsupported/not_configured | `tests/test_server.py::test_disabled_capability_returns_before_building_its_tool_client`, `tests/test_tickets.py::test_custom_object_projection_requires_its_capability`, 조건부 설정 테스트 | 로컬 capability 차단 회귀 근거. 실제 계정별 제품/플랜별 응답은 제한적으로만 확인했다. |
| 22 | Guide 브랜드·locale·ID wire type | `tests/test_guide.py::test_article_publish_binds_brand_to_approval_and_uses_its_subdomain`, `tests/test_guide.py::test_article_translation_accepts_a_string_help_center_id`, locale 테스트 | 회귀 근거. |
| 23 | body upsert 거부·별도 파괴적 교체·locale publish | `tests/test_guide.py::test_translation_upsert_refuses_to_replace_an_existing_body`, `tests/test_guide.py::test_translation_body_replacement_is_destructive_and_separate`, `tests/test_guide.py::test_publish_translation_requires_public_approval_and_reads_back` | 회귀 근거. 실제 content block 손실 여부를 테스트 계정 없이 실험하지 않는다. |
| 24 | draft=false create/upsert 거부·공개 title·draft 전환 gate | `tests/test_guide.py::test_article_create_rejects_publishing_before_any_request`, `tests/test_guide.py::test_upsert_of_published_translation_requires_public_and_destructive_gates` | 회귀 근거. |
| 25 | 이미지·badge upload 승인 전 outbound 0 | `tests/test_community.py::test_user_image_upload_requires_approved_external_upload_and_safe_local_file`, `tests/test_community.py::test_badge_icon_upload_uses_the_secure_external_upload_flow` | 회귀 근거. 실제 presigned 업로드 E2E는 보류. |
| 26 | 혼합 user votes·scan resume·구독 upsert/read-back | `tests/test_community.py::test_community_user_vote_cursor_resumes_an_unconsumed_mixed_page`, `tests/test_community.py::test_user_subscription_upsert_previews_change_and_reads_back_after_approval`, `tests/test_community.py::test_subscription_write_with_unverified_readback_is_unknown_without_retry` | 회귀 근거. include_comments가 없는 실응답을 임의로 검증 성공 처리하지 않는다. |
| 27 | CSAT backend 필터·epoch 단위 | `tests/test_guide.py::test_csat_adapters_only_send_their_official_filters`, `tests/test_guide.py::test_survey_list_and_export_use_official_suffixless_path` | 회귀 근거. survey 실제 0건 읽기 이력은 유효 응답 다중 페이지의 실증이 아니다. |
| 28 | 비밀 없는 status/locales·무설정 무네트워크 handshake | `tests/test_server.py::test_connection_status_does_not_expose_credentials`, `tests/test_handshake.py::test_unconfigured_server_completes_an_mcp_handshake_without_network_access` | mock status 및 socket 연결 금지 상태의 실제 stdio handshake 근거. |
| 29 | Macro partial_success·복구 정보 | `tests/test_tickets.py::test_macro_unknown_write_outcome_includes_recovery_without_replay`; `TicketTools.apply_macro` | 추가 대조: 현재 테스트는 unknown 및 복구 안내를 검증한다. 구현은 partial 오류를 받으면 복구 정보를 보강하지만, 실제 transport가 partial_success를 판정하는 경로의 근거는 아직 없다. 이를 부분 성공 검증 완료로 간주하지 않는다. |
| 30 | Macro 동적 위험 승격 | `tests/test_tickets.py::test_ticket_macro_raises_all_gates_from_its_actions`, `tests/test_tickets.py::test_macro_risks_include_effective_preview_impersonation_and_closure` | 회귀 근거. |
| 31 | Python 3.10–3.12 test→wheel→MCP CI | `.github/workflows/ci.yml`, CI `34671700267` | 해당 구현 커밋에서 Linux 3버전 전체 순서 성공. macOS/Windows는 별도 package smoke만 성공. |
| 32 | 로그에 token·Authorization·고객 본문 없음 | `tests/test_server.py::test_tool_call_writes_a_redacted_audit_event`, `tests/test_client.py::test_scope_denial_reports_broad_read_requirement_without_leaking_body`, `audit.py` | 허용 필드 기반 로그 및 오류 회귀 근거. 임의 입력 전체의 비밀 탐지/DLP 보장이 아니다. |
| 33 | ResourceLink·ImageContent·경로/base64 제한 | `tests/test_server.py::test_attachment_download_result_includes_a_resource_link`, `tests/test_server.py::test_attachment_image_result_uses_image_content`, export resource 테스트 | MCP 경계 회귀 근거. |
| 34 | 설치·인증·scope·gate·migration·복구 문서 | `README.md`, `README.ko.md`, `.env.example`, `docs/migration.md` | 파일과 내용 존재. 설치 안내 후속 수정 및 관련 CI는 누적 감사 참조. |
| 35 | OAuth rollback 최신 회전 credential·오래된 backup 금지 | `docs/migration.md`의 OAuth store migration/Package rollback, `tests/test_auth.py::test_refresh_preserves_saved_connection_identity` | 운영 절차 문서와 회전 저장 회귀는 별개 근거. 실제 rollback 훈련 완료를 주장하지 않는다. |
| 36 | 운영 계정 무승인 쓰기 E2E 금지 | 현재 작업은 MockTransport/합성 파일/로컬 승인 테스트; 실제 작업 이력은 누적 감사 | 계속 유지할 작업 경계. 운영 계정 쓰기를 검증 대체 수단으로 사용하지 않는다. |

## 다음 확인 순서

1. 02: PRD 7장의 승인된 기능을 원본 비교 표면과 canonical 도구/리소스/프롬프트에
   연결한다. 특히 시간 추적·Git-Zen·KB·프롬프트처럼 도구 개수에 가려지는 항목을 확인한다.
2. 15·20·29: 복합 요구의 하위 항목별 근거를 대조한다. 특히 Macro의 단일 PUT
   구현에서 확인 가능한 부분 성공과 단순 결과 불명을 구분한다. 재현되는 구현/테스트 누락만
   수정하며 기존 기능을 넓히거나 범위를 축소해 통과시키지 않는다.
3. 11: 설치 검증과 read-only 보안 검증이 같은 배포 코드에 적용됨을 연결한다.
4. 13 등 실제 쓰기/수동 운영 검증은 보류로 유지한다. 계정 재요청·운영 쓰기·병합·
   출시·배포를 자동으로 수행하지 않는다. 이 보류를 코드 검토의 차단 사유로 삼지 않는다.

이 색인으로 PRD 체크박스를 일괄 완료 처리하지 않는다. 회귀 근거의 실제 assertion과
요구 범위를 대조한 뒤에만 개별 수용 판단을 갱신한다.
