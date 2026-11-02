# 브라질 이커머스 & D2C 프로모션 대시보드

LG 중남미 D2C팀용 도구입니다. 매일 1회 브라질 쇼핑몰 6곳과 경쟁 제조사 공식몰 4곳의 프로모션을 수집합니다.
결과는 사내 대시보드(HTML)로 만들고, Microsoft Teams로 요약 알림을 보냅니다.

- **이커머스**: Mercado Livre, Magazine Luiza, Fast Shop, Carrefour, Webcontinental, Amazon
- **경쟁 D2C**: Samsung, Electrolux, Midea, TCL (LG.com/br은 기준선으로 함께 수집)
- **카테고리**: TV, 냉장고, 세탁기

## 동작 방식

```
[매일 06:00 BRT] run_daily.py
  ① Playwright(헤드리스 Chrome) → 사이트별 메인/오퍼 + TV/냉장고/세탁기 페이지 스크린샷 + 화면 텍스트
  ② Claude(비전) → 배너·쿠폰·캐시백·PIX·무이자할부 등 캠페인 + 상품 가격을 JSON으로 판독
       (어제 캠페인 목록을 같이 넘겨 '신규 / 계속 / 종료'를 판단)
  ③ SQLite(data/monitor.db)에 일별 누적
  ④ 전일 대비 비교 → output/index.html 대시보드 (+ output/archive/날짜.html)
  ⑤ Teams 채널에 요약 카드 발송
```

## 대시보드 구성

- **일일 현황 탭**: 오늘 기준 신규/종료 프로모션, 가격 하락, 할인 강도, 수집 상품 전체
- **52주 프로모션 캘린더 탭**: 행=사이트, 열=주차(ISO) 또는 일자. 연도 이동과 카테고리 필터를 지원합니다.
  - 매일 기록된 캠페인을 '계속(continuing)' 판정으로 이어 붙여 **프로모션 블록**을 만듭니다.
  - 블록 기간은 처음 확인한 날 ~ 마지막으로 확인한 날입니다. 가장 최근 수집에서도 보이면 '진행 중'으로 표시합니다.
  - 같은 사이트의 메인·카테고리 페이지에 같은 문구로 동시에 걸린 행사는 한 블록으로 합칩니다.
  - 블록을 누르면 할인·조건·유형·브랜드 등 상세 정보가 나옵니다.
- **한국어/English 전환**: 우측 상단 버튼(선택은 브라우저에 기억). `index.html?lang=en`으로 영어 링크 공유 가능.
  캠페인명·조건·페이지 메모·요약은 AI 판독 단계에서 영어도 함께 생성합니다(이전 데이터는 영문 대신 포르투갈어 원문 표시).

HTML 구조를 파싱(셀렉터)하지 않고 화면을 AI가 읽는 방식입니다. 그래서 사이트가 개편돼도 코드 수정이 거의 필요 없습니다.

## 설치 (사내 서버/PC, Python 3.10+)

```bash
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium   # 사내망에서 막히면 .env에 CHROMIUM_PATH로 설치된 Chrome 지정
cp .env.example .env          # API 키, Teams 웹훅 URL 입력
```

## 첫 실행 순서

1. **URL 점검 (AI 호출 없음, 비용 0)**: `python check_urls.py`
   - `output/url_check/*.jpg`를 열어 의도한 페이지인지 확인합니다.
   - `config.yaml`의 URL은 공개 URL 패턴으로 만든 초안이라, 일부는 실제 주소로 바꿔야 할 수 있습니다.
2. **사이트 1~2곳만 시험**: `python run_daily.py --sites magalu,samsung --no-teams`
   - 결과는 `output/index.html`에서 확인합니다.
3. **전체 실행 확인 후 스케줄 등록**
   - Linux: `run_daily.sh` 상단의 crontab 예시
   - Windows: `run_daily.bat` 상단의 schtasks 예시 (한국 18:00 = 브라질 06:00)

## Teams 알림 설정

Office 365 커넥터(Incoming Webhook)는 종료되었습니다. Teams **Workflows**를 사용합니다.

1. 알림 받을 Teams 채널 → `…` → **Workflows**
2. 템플릿 **"웹후크 요청이 수신되면 채널에 게시"** (Post to a channel when a webhook request is received) 선택
3. 생성된 URL을 `.env`의 `TEAMS_WEBHOOK_URL`에 넣습니다.
4. `config.yaml`의 `teams.dashboard_url`을 대시보드를 올린 사내 주소로 바꿉니다. 카드의 "대시보드 열기" 버튼이 이 주소를 엽니다.

대시보드 공유 방법:
- `output/` 폴더를 사내 웹서버(IIS/nginx)나 SharePoint 문서 라이브러리에 올립니다.
- 또는 공유 드라이브에 두고 `index.html`을 엽니다. 스크린샷이 상대경로라 폴더째 옮기면 됩니다.

## 운영 시 유의사항

- **브라질 IP 권장**: 일부 사이트는 해외 IP를 차단하거나 다른 가격·행사를 보여줍니다.
  - 한국 서버에서 `blocked`가 많이 나오면 LG 브라질 법인 서버에서 실행하세요.
  - 또는 브라질 프록시를 `HTTPS_PROXY`에 지정하세요.
- **Amazon**은 봇 차단이 가장 강하고, 이용약관상 자동 수집을 제한합니다. 수집 실패가 잦거나 법무 검토 결과가 부정적이면 `config.yaml`에서 해당 블록을 지우면 됩니다.
- **법무 검토 권장**: 하루 1회, 페이지 간 5초 간격으로 부하를 최소화했습니다. 그래도 경쟁사 사이트 자동 수집이므로 사전 확인을 권장합니다.
- **CEP(우편번호)**: 가격·배송비가 지역마다 다릅니다. 현재는 각 사이트의 기본 노출 가격을 수집하며, CEP 자동 입력은 구현하지 않았습니다.
- **AI 판독 오차**: 숫자를 잘못 읽을 수 있습니다. 대시보드의 스크린샷과 "원본 페이지" 링크로 확인하세요.

## 비용 (추정, 실측 필요)

- 대략 하루 40페이지 × 페이지당 약 $0.1~0.2 = **하루 $5~8 수준**으로 추정합니다.
- 실제 비용은 첫 주 Anthropic 콘솔 사용량으로 확인하세요.
- 줄이는 방법:
  - `config.yaml`에서 `effort: low`로 설정
  - `capture.max_tiles`를 2~3으로 축소
  - `model`을 `claude-sonnet-5-5`로 변경 (정확도는 시험 후 판단)

## 사이트/카테고리 추가

`config.yaml`의 `sites`에 블록을 추가합니다. `pages`의 키는 `home`, `tv`, `refrigerator`, `washer`입니다.

카테고리를 추가하려면(예: 에어컨) 다음 세 곳을 함께 수정합니다.
- `monitor/extract.py`의 `CATS`와 프롬프트
- `monitor/report.py`의 `CAT_KO`, `PAGE_KO`
