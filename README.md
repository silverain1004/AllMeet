 # GCP 초기 세팅
 gcloud --version
 gcloud auth login
 gcloud config set project ai-agent-test-482706

# 연결된 프로젝트 확인
gcloud config get-value project

# 계정 확인
gcloud auth list

 # GCP 배포
 gcloud run deploy all-meet-agent --source . --region asia-northeast3 --allow-unauthenticated

# 응답 지연 관련 배포 옵션 (선택)
# --min-instances=1 : 콜드 스타트 제거. 인스턴스가 상주하므로 인메모리 캐시
#                     (회의실·멤버·OAuth 토큰·freebusy 스냅샷)도 계속 살아 있어
#                     카드 클릭 응답이 눈에 띄게 빨라진다. 상시 과금이 붙는다.
# --concurrency=8   : functions-framework 기본 gunicorn 은 worker 1 · thread 8 이라
#                     Cloud Run 기본 동시성 80 을 그대로 두면 요청이 스레드 큐에 쌓인다.
# gcloud run deploy all-meet-agent --source . --region asia-northeast3 \
#   --allow-unauthenticated --min-instances=1 --concurrency=8