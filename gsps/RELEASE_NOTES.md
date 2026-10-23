# DICOM + GSPS Viewer — 릴리즈 노트

## v1.1 (2026-05-14)

### 신규 기능

- **GSPS 오버레이 토글**: 영상 캔버스 우측 상단에 On/Off 버튼 추가. 기본값 On이며, Off 전환 시 오버레이 없이 원본 DICOM 영상만 표시.
- **폴리라인 색상 구분**: 직선 계열 주석(CTR 측정선 등)은 파란색(`#00ABF3`), 곡선 ROI 윤곽선은 빨간색(`#F47378`)으로 구분 표시. 공선성(collinearity) 검사와 닫힌 폴리라인 판별을 조합하여 분류.
- **Value 셀 복사 지원**: 태그 테이블의 Value 열을 더블클릭하면 읽기 전용 편집 모드로 진입하여 전체 값(말줄임 없음)을 확인하고 Ctrl+A / Ctrl+C로 복사 가능.
- **영상 해상도 표시**: 캔버스 하단 레이블을 정적 텍스트 "Pixel X" 대신 실제 영상 해상도(`{가로}(c) × {세로}(r) px`)로 표시.

### 버그 수정

- "pleural space" 이외의 텍스트 오브젝트에 바운딩 박스 좌표가 없을 경우 발생하던 `NameError` 수정.
- PIXEL 단위 앵커 포인트를 항상 이미지 크기로 스케일링하던 오류 수정. 태그 `(0070,0004)` Anchor Point Annotation Units를 읽어 DISPLAY(정규화)와 PIXEL(픽셀 좌표)을 구분 처리.
- 아이콘 경로 하드코딩 문제 수정. `get_resource_path()`를 사용하여 PyInstaller 번들 환경에서도 정상 동작.
- `SOPClassUID` 판별을 `.name` 속성 문자열 비교에서 UID 문자열(`1.2.840.10008.5.1.4.1.1.11.1`) 직접 비교로 변경하여 안정성 향상.
- 닫힌 폴리라인(시작점 ≈ 끝점)이 방향 벡터 영벡터로 인해 직선으로 오판되던 문제 수정. 공선성 검사 전 닫힌 폴리라인 여부를 먼저 판별.

### 개선 사항

- GSPS 텍스트 주석 키워드 필터 제거. `TextObjectSequence`의 모든 텍스트가 표시되며, `pleuraleffusion`, `consolidation` 등 질환명 레이블도 정상 출력.
- 전체 클래스·메서드·모듈 수준 함수에 타입 힌트 및 Google 스타일 Docstring 추가.
- 미사용 `colormath` import(`LabColor`, `sRGBColor`, `convert_color`) 제거.
- 디버그용 `print` 구문 전체 제거.
- 중복된 `PyQt5.QtGui` import 블록 통합 및 미사용 심볼(`QFont`, `QCursor`, `QLabel`) 제거.
