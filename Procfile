# Cloud Run 컨테이너 기동 시 실행할 프로세스 (빌드팩이 "web" 프로세스 사용).
# Functions Framework: main.py 의 @functions_framework.http 함수 hello_http 를 HTTP로 노출.
# $PORT 는 Cloud Run 이 자동 주입 (반드시 이 포트로 바인딩).
web: functions-framework --target hello_http --port $PORT
