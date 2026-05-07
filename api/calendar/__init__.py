"""Google Calendar v3 — 이벤트 조회 generic CRUD.

공용 캘린더(SA 공유) 와 개인 캘린더(user_oauth) 모두 같은 함수로,
``credentials_source`` 인자로 분기. 도메인 키워드(주간회의 등) 는 호출 측에서.
"""
