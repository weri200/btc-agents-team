# BTC Agents Team · Futures Desk

Binance 코인 선물의 **단타와 스윙을 별도로 분석하고, AI 팀원들의 판정과 근거를 GUI에서 검토**하는 프로젝트입니다. 자동 주문보다 사람이 분석 과정을 확인하는 경험을 우선합니다.

> **현재 상태: 데모 GUI 프로토타입.** 실제 Binance 시세, GPT/로컬 모델, OpenBB, CCXT, TradingAgents, NautilusTrader는 아직 연결되지 않았습니다. 화면의 기본 판정은 가상 예시이며 투자 신호가 아닙니다.

## 다른 채팅에서 이어서 작업하기

이 저장소를 열고 다음과 같이 요청하면 됩니다.

> README.md와 dist/ 소스를 먼저 확인하세요. 이 프로젝트는 Binance 선물의 단타·스윙 분석팀 GUI입니다. 현재는 데모이고 실제 분석 백엔드는 미구현입니다. 구현된 기능을 보존하면서 다음 작업을 진행해 주세요.

저장소에는 소스와 프로젝트 현황이 들어 있습니다. **이전 채팅 전체나 브라우저에 저장한 보고서 이력은 자동으로 공유되지 않습니다.** 비공개 저장소를 읽으려면 해당 채팅의 GitHub 연결에 접근 권한이 있거나 로컬 체크아웃을 열어야 합니다.

## 목표와 현재 결정

- 거래소: Binance. 화면은 USDⓈ-M 무기한 선물을 기준으로 설계했습니다. 사용자가 확정한 것은 Binance 선물이며, 세부 상품은 구현 전에 확인해야 합니다.
- 분석 주기: 단타와 스윙을 모두 제공하고 서로 다른 의견을 그대로 보여줍니다.
- 모델: GPT를 기본 후보로 두며 로컬 모델도 사용할 수 있도록 결과 형식을 통일합니다.
- 투명성: 팀원별 판정, 근거, 반론, 판단을 철회할 조건, 자료 기준 시각을 표시합니다.
- 자동 주문: 현재 범위에 포함하지 않습니다.
- TradingAgent는 TauricResearch/TradingAgents를 뜻한다고 가정한 상태입니다.

## 구현된 기능

| 기능 | 상태 |
| --- | --- |
| 단타·스윙 종합 판정 비교 | 데모 구현 |
| 기술·파생상품·뉴스/거시·반대 검토자별 카드 | 데모 구현 |
| 팀원별 근거·반론·무효 조건·모델·출처 상세 보기 | 구현 |
| 공통 의견·남은 이견·재검토 조건 표시 | 구현 |
| JSON 보고서 가져오기·내보내기 | 구현 |
| 보고서 형식 검증 | 구현 |
| 가져온 보고서 이력 | 브라우저 localStorage에 최대 20개 |
| 실제 데이터 수집·LLM 분석·백테스트 | 미구현 |

종목을 바꾸면 해당 종목 이름의 데모 화면으로 전환됩니다. 종목별 시장을 실제 분석하는 기능은 아닙니다. 가져온 보고서는 판단 이력에서 다시 열 수 있습니다.

## 빠른 시작

별도 패키지 설치나 빌드가 필요 없는 HTML/CSS/JavaScript 프로젝트입니다.

```sh
git clone https://github.com/weri200/btc-agents-team.git
cd btc-agents-team
python -m http.server 8765 --bind 127.0.0.1 --directory dist
```

브라우저에서 `http://127.0.0.1:8765`를 엽니다. Python 없이 `dist/index.html`을 직접 열 수도 있지만, 일관된 브라우저 저장소 동작을 위해 로컬 서버를 권장합니다.

검사에는 Node.js가 필요합니다. 별도 npm 의존성은 없습니다.

```sh
node test.cjs
node --check dist/app.js
node --check dist/model.js
```

예시 보고서와 판정 집계, 실제 분석 보고서 형식, 잘못된 날짜·판정·URL·중복 팀원 ID 등의 거절을 검사합니다.

## 파일 구조

```text
dist/
  index.html          화면 구조
  style.css           반응형 스타일
  model.js            데모 데이터, 보고서 검증, 판정 집계
  app.js              화면 전환, 상세 보기, 파일 입출력, 이력
example-report.json   연동 형식 예시 (실제 분석 아님)
test.cjs              의존성 없는 검증 테스트
README.md             목적, 현황, 실행 및 인수인계
```

`dist/`는 빌드 산출물이 아니라 현재 직접 관리하는 정적 소스입니다.

## 분석 백엔드 구성안 — 미구현

| 도구 | 예정 역할 |
| --- | --- |
| OpenBB | 필요한 거시경제·뉴스 등 배경 자료 수집 |
| CCXT | Binance 선물 가격·펀딩비 등 지원 데이터 수집 |
| TradingAgents | 코인 선물용 분석 역할·반대 검토·종합 보고서 생성 |
| NautilusTrader | 수수료·펀딩·슬리피지 및 체결 가정을 포함한 전략 검증 |

Python 분석 서비스에서 GPT 또는 로컬 모델을 호출하고, 공통 JSON 보고서로 GUI에 전달하는 구성을 고려합니다. 각 라이브러리의 버전·호환성과 공급자별 데이터 접근 권한은 실제 연동 단계에서 검증해야 합니다.

단타의 5분/15분/1시간, 스윙의 4시간/1일 봉은 **초기 화면 가정**입니다. 검증된 전략이나 확정된 분석 주기가 아닙니다.

## 보고서 형식

전체 예시는 `example-report.json`, 검증 규칙은 `dist/model.js`의 `validate()`를 확인하세요.

- `schemaVersion`: `1`
- `id`, `symbol`, `asOf`, `promptVersion`: 보고서 식별 정보
- `exchange`: `Binance`
- `market`: `USDⓈ-M perpetual`
- `mode`: `demo` 또는 `analysis`
- `horizons.intraday`, `horizons.swing`: 두 주기의 독립된 보고서
- 주기별 `verdict`, `summary`, `agreement`, `disagreement`, `condition`, `agents`
- 팀원별 `id`, `name`, `role`, `verdict`, `summary`, `evidence`, `counter`, `invalidation`, `model`, `sources`
- 판정: `long`, `short`, `wait`
- 출처: `title`, `url` (HTTP/HTTPS 또는 null), `asOf`

날짜에는 시간대가 필요하며 UI는 한국 시간으로 표시합니다. `mode: analysis`는 파일 작성자의 선언일 뿐, 실제 AI 실행이나 내용 정확성을 인증하지 않습니다. 파일은 최대 500KB이며 가져오기는 서버 업로드 없이 브라우저에서 처리합니다.

## 다음 구현 순서

1. Binance 선물 상품과 분석 주기, GPT 모델·API 또는 로컬 모델 실행 환경 확정.
2. CCXT 기반 데이터 수집, 마감 봉·누락·시각 검증.
3. 필요한 OpenBB 공급자 연결 및 뉴스 출처 검증.
4. TradingAgents의 코인 선물용 역할과 도구 구성, GPT/로컬 모델로 실제 보고서 생성.
5. 분석 API와 GUI 연결, 실패·지연·자료 부족 표시.
6. NautilusTrader 백테스트와 실제 시간 순서의 모의 검증.

추가 디자인 요청으로 밝은 배경, SF Pro 계열 타이포그래피, 부드러운 카드, 반투명 하단 입력창, 은은한 그라데이션 말풍선을 가진 iOS 스타일 AI 채팅 화면이 언급되었습니다. 해당 이미지 생성 작업은 중단되어 **구현된 결과가 없으며**, 현재 데스크의 확정된 교체 디자인으로 간주하지 않습니다.

## 배포와 데이터 관리

기존 비공개 GUI: https://futures-research-desk-m1022.weri-200.chatgpt.site/

GitHub 푸시는 해당 사이트를 자동 갱신하지 않습니다. 기존 Sites 배포 설정과 Git 이력은 이 저장소로 복사하지 않았습니다. GitHub는 이후 개발 소스의 기준으로 사용하고, 배포 연동은 별도로 구성해야 합니다.

API 키·비밀키는 저장소나 브라우저에 넣지 않습니다. 실제 보고서와 계좌 자료는 기본적으로 커밋하지 않습니다. `.gitignore`에 로컬 설정, 보고서, 수집 데이터 및 런타임 폴더를 제외했습니다.

## 검증의 한계

GUI와 입력 검증이 동작해도 매매 전략의 수익성을 의미하지 않습니다. 다수 의견이나 모델의 자신감은 승률이 아닙니다. 과거 공개 시각, 미래 정보 유입, 거래 비용과 선물 청산 위험을 검증해야 합니다. 실제 투자 및 자동 주문에 바로 사용할 수 있는 상태가 아닙니다.
