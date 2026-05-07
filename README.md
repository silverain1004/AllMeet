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
