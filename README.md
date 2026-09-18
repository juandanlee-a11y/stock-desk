# stock-desk

개인 관심종목을 한 화면에서 확인하는 정적 대시보드입니다. `watchlist.json`에 종목을 추가하면
시세, 52주 밴드, 목표주가, 공시·뉴스 피드가 자동으로 카드로 만들어집니다.

화면은 세 층으로 되어 있습니다.

1. **시장 온도계** — 버핏 지수 · 실러 CAPE(월 1회 보는 밸류에이션) + VIX · 미국 10년물 · 달러인덱스 · 원/달러(매일 보는 방향 신호).
   구간(저평가/적정/주의/과열)과 현금 비중 가이드는 `watchlist.json`의 `thermometer`에서 본인 기준으로 바꿉니다.
2. **매수 규칙 카드** — 종목별 `rules`(왜 사는가 / 해자 / 매도 신호 / 손절가 / 익절가 / 평단 / 점검일)를 미리 적어두면
   카드에 표시되고, 현재가가 손절가·익절가에 닿거나 점검일이 지나면 상단에 경보가 뜹니다.
3. **관심종목 스냅샷 · 수익률 · 공시/뉴스** — 기존과 같습니다.

## 구성

- `watchlist.json` — 추적할 종목 목록 (여기만 고치면 대시보드가 따라옵니다)
- `fetch.py` — watchlist를 읽어 시세(Yahoo Finance) · 목표주가 · 한국 공시(DART) ·
  미국 공시(SEC EDGAR) · 뉴스를 모아 `data.json` / `data.js`로 저장
- `index.html` — `data.js`를 읽어 렌더링하는 화면 (정적 파일, 서버 불필요)
- `.github/workflows/update.yml` — 평일 하루 세 번 자동으로 `fetch.py`를 실행해 데이터를 갱신

## 로컬에서 보기

네트워크 없이 화면만 먼저 확인하려면:

```bash
pip install yfinance pandas requests
python fetch.py --demo   # 샘플 데이터 생성
```

그 다음 `index.html`을 브라우저로 열면 됩니다.

실제 데이터를 받으려면:

```bash
python fetch.py
```

## 온도계 데이터 출처

| 지표 | 자동 수집 | 비고 |
|---|---|---|
| VIX · 미국 10년물 · 달러인덱스 · 원/달러 | Yahoo Finance (`^VIX`, `^TNX`, `DX-Y.NYB`, `KRW=X`) | 20일 추세선 포함 |
| 실러 CAPE | multpl.com 현재값 | `thermometer.manual_cape.value`가 있으면 그 값 우선 |
| 버핏 지수 | Wilshire 5000(Yahoo `^W5000`) ÷ FRED 명목 GDP | 근사값. `thermometer.manual_buffett.value`에 gurufocus 등에서 본 값을 넣으면 그 값 우선 |

자동 수집이 실패하면 직전 값을 유지하고 "갱신 안 됨"으로 표시합니다. 수동값은 월 1회만 갱신해도 충분합니다.

## 환경 변수 (선택)

| 변수 | 용도 | 없으면 |
|---|---|---|
| `DART_API_KEY` | 한국 공시(DART) 조회. https://opendart.fss.or.kr 무료 발급 | 한국 공시 수집을 건너뜁니다 |
| `SEC_UA` | 미국 SEC EDGAR 요청용 User-Agent (예: `"Dana dana@example.com"`) | 미국 공시 수집을 건너뜁니다 |

로컬에서는 저장소 루트에 `.env` 파일로 넣어두면 `fetch.py`가 자동으로 읽습니다.
(`.env`는 `.gitignore`에 등록되어 있어 저장소에 올라가지 않습니다.)

## 자동 갱신

`update.yml`이 평일 한국시간 08:30 / 16:00 / 23:30에 GitHub Actions로 `fetch.py`를 실행하고,
바뀐 데이터(`data.json`, `data.js`, `data.prev.json`)를 저장소에 자동 커밋합니다.
Actions 탭에서 수동으로도 실행할 수 있습니다.

## 참고

이 화면은 참고용으로 자료를 모아 보여줄 뿐입니다. 무료 시세는 지연 데이터이고,
목표주가는 증권사의 전망일 뿐 보장이 아니며, 매매 판단을 대신하지 않습니다.
