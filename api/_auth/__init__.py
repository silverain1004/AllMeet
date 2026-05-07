"""외부 서비스 인증 헬퍼.

- `service_account` — Cloud Run ADC 또는 키 파일 기반 SA credentials (Drive/Calendar 공용/Chat REST).
- `confluence_basic` — `config/team_list.global` 의 user_email + api_token 으로 Basic auth.
- `user_oauth` — Phase 2: 사용자 refresh_token 으로 access_token 재발급 (Gmail/개인 Calendar).

CONVENTIONS.md §11.1 — 도메인 키워드 금지. generic 인증만.
"""
