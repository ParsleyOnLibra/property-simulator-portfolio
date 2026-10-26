# Property × Equity

집 한 채의 가격이 주식 몇 주에 해당하는지 비교하는 프로젝트입니다.

**공개 데모:** [GitHub Pages에서 열기](https://parsleyonlibra.github.io/property-simulator-portfolio/)

`docs/`는 브라우저에서 실행되는 공개 데모입니다. 기본 아파트 가격, 주가, 환율은 모두 **가상 예시**이며 실제 시세가 아닙니다. 직접 준비한 UTF-8 CSV 세 개를 브라우저에서 불러와 비교할 수 있습니다. CSV는 서버에 업로드하거나 저장하지 않습니다. 공개 데모에는 자동 시세 수집 기능이 없습니다.

전체 Python 버전은 [`property_simulator_v07/`](property_simulator_v07/)에 있습니다. 이 버전은 로컬 서버를 실행해 데이터 수집과 저장을 지원합니다. 사용법과 CSV 형식은 [v0.7 안내](property_simulator_v07/README.md)를 참고하세요.

## GitHub Pages 배포

저장소 **Settings → Pages → Build and deployment**에서 **Deploy from a branch**를 선택하고, 브랜치는 `main`, 폴더는 `/docs`로 설정한 뒤 **Save**를 누릅니다. 공개 주소는 위 데모 링크입니다.

v0.7 화면을 수정한 뒤 공개 데모를 다시 만들려면 저장소 루트에서 `python scripts/build_pages.py`를 실행하세요. 계산 로직은 [`docs/static-api.js`](docs/static-api.js)에 별도로 구현되어 있으므로 Python 계산 방식이 바뀌면 함께 검토해야 합니다.
