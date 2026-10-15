"""결과 태그별 스펙 설명(행 번호 툴팁)."""
import html


# 행 번호 툴팁: dcm_tag_summary.xlsx의 내용·영향범위 (gravuty 코드 기준, "※ 엑셀 정의와 차이" 문구 제외)
TAG_TOOLTIPS: dict[str, tuple[str, str]] = {
    "0008,0012": (
        "분석 후 결과를 만든 날짜(YYYYMMDD). SC는 AI 분석 응답 시점, 나머지는 결과 생성 시점.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0008,0013": (
        "분석 후 결과를 만든 시각(HHMMSS). 기준은 Instance Creation Date와 동일.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0008,0018": (
        "기본: VUNO UID \"1.2.410.200123.100.3.{Series Number}.{UNIX time(UTC)}.{난수 1~32767}.{Instance Number}\". 'Add an instance number to the original SOP instance UID' 옵션 사용 시 \"{원본 SOP UID 앞 51자}.{Instance Number}\". 압축 후에도 원래 값으로 다시 설정.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0008,0021": (
        "분석 후 결과를 만든 날짜(YYYYMMDD).",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0008,0064": (
        "\"WSD\" 고정.",
        "SC, SC Report",
    ),
    "0008,0070": (
        "\"VUNO\" 고정.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0008,0090": (
        "원본 값을 그대로 유지해야 하나(요구사항), 현재 값을 넣는 코드가 없어 결과물에 생성되지 않음.",
        "SC, GSPS, SC Report, Encap PDF – 현재 미생성",
    ),
    "0008,1030": (
        "원본 값 복사. SC는 원본에 없으면 빈 값으로 생성.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0008,103E": (
        "\"VUNO Med Analysis Result\" 고정.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0008,1090": (
        "제품 번호. CXR=VN-M-02, Fundus=VN-M-03, CXR Triage=VN-M-30, CXR Assist=VN-M-31.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0018,1020": (
        "Gravuty 버전(환경변수 GRAVUTY_VERSION, 현재 1.3.3).",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0020,000E": (
        "① 기본: VUNO UID \"1.2.410.200123.100.2.{Series Number}.{UNIX time(UTC)}.{난수 1~32767}\" (최대 64자)\n"
        "② 'Add a serial number to the original series instance UID' 옵션: \"{원본 Series UID 앞 57자}.{Series Number}\"\n"
        "③ 'Use the original series instance UID' 옵션: 원본 그대로 (③이 ②보다 우선)\n"
        "Fundus는 눈(좌/우)마다 별도 UID가 만들어짐. GSPS의 Referenced Series Sequence 안에는 원본 Series UID가 들어감.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0020,0010": (
        "원본 값 복사. SC는 원본에 없으면 빈 값으로 생성.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0020,0011": (
        "SC=900010, GSPS=900090, SC Report=900070, Encap PDF=900080 고정.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0020,0013": (
        "원본 Instance Number(없으면 1) 기준으로 계산.\n"
        "SC = 원본×100+1\n"
        "GSPS = 원본×100000+1\n"
        "SC Report = 원본×1000+1부터 PDF 페이지마다 +1\n"
        "Encap PDF = 원본×10000+1",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "0020,4000": (
        "판독 문구.\n"
        "• CXR: \"Result: \" + 판독 문장들을 \", \"로 연결(문장 끝 마침표 제거). Medisol 옵션 시 판독 문장 목록을 그대로 넣음('\\'로 구분된 여러 값).\n"
        "• Triage: \"<소견> is suspected in <민감도들> sensitivity settings. (Not for diagnostic use)\" / 정상 \"Processed (Not for diagnostic use)\"\n"
        "• Assist: \"Abnormal findings in <ROI>.\" (CTR 있으면 \" Cardiothoracic Ratio is <값>\" 추가) / 정상 \"No detectable findings.\"\n"
        "• Fundus: \"Result: Abnormal / No findings / Ungradable\", 양안 리포트 \"Result: Both Eyes\", 소견별 SC \"Result: <소견명>\"",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "1001,1002": (
        "각 제품 버전(예: 1.0.1)이 들어가야 하나, 현재 설정값이 비어 있어 빈 값으로 들어감.",
        "SC, GSPS, SC Report, Encap PDF – 현재 빈 값",
    ),
    "1001,1004": (
        "분석 성공 여부. 현재 항상 \"1\"(실패 시에는 결과물 자체를 만들지 않음).",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "1001,1005": (
        "\"1\" 고정. 현재 CXR Link 여부와 관계없이 모든 제품에 들어감.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "1001,1008": (
        "분석 요약 JSON. 환자명이 없으면 \"<EMPTY>\".\n"
        "• CXR: {\"report\": \"<환자명> has findings : Nodule (57), Consolidation (97)[, cardiothoracic ratio(50)]\"} / {\"report\": \"<환자명> has no findings\"}. Medisol 옵션 시 SC·GSPS는 JSON 없이 \"<환자명> Abnormal\" / \"<환자명> No findings\"\n"
        "• Triage: {\"report\": \"<환자명> patient has findings: A,B\"} / {\"report\": \"<환자명> patient has been processed.\"}\n"
        "• Assist: {\"report\": \"<환자명> has findings : <결과 1행>\"} / {\"report\": \"<환자명> has no findings\"}. SC Report·PDF는 \"{}\"(빈 값)\n"
        "• Fundus: {\"report\": \"<환자명> patient has findings : A and B.\"} / \"… patient has no finding.\" / \"… patient is ungradable.\"; 양안 리포트는 눈별 문구를 줄바꿈으로 합침",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "1001,1009": (
        "서비스 레벨. CXR=3, Triage=2, Assist=2, Fundus=2.",
        "SC, GSPS, SC Report, Encap PDF",
    ),
    "1001,1011": (
        "대표 결과값.\n"
        "• CXR: {\"result\": \"Abnormal\" 또는 \"No findings\", \"p\": 확률×100 정수}\n"
        "• Fundus: {\"result\": \"Abnormal\" / \"No findings\" / \"Ungradable\", \"p\": 값}\n"
        "• Triage: \"Suspected\" / \"Processed\"\n"
        "• Assist: 하단 결과 첫 줄(ROI 정보) / \"No findings\" (JSON 형식 아님)",
        "SC·GSPS , SC Report·Encap PDF (CXR, Fundus 단안)",
    ),
    "1001,1015": (
        "소견별 JSON. Findings Sequence(1001,1010) 각 항목 안에 들어가며, SC는 최상위에도 {\"finding\": [소견 목록]}으로 한 번 더 들어감(CXR, Triage).\n"
        "• CXR: {\"name\": 소견명, \"p\": 확률, \"heatmap\": \"\", \"roi\": \"\"} / 정상 {\"name\": \"no finding\", \"p\": 0.0, \"heatmap\": \"\", \"roi\": \"\"}\n"
        "• Triage: SC {\"name\": 소견명} / {\"name\": \"processed\"}, GSPS {\"name\": 소견명, \"sensitivity\": 민감도} (정상 결과일 때 글자 단위로 쪼개져 들어가는 오류 있음)\n"
        "• Fundus: {\"name\": 소견명, \"p\": 확률, \"roi\": 이미지(base64), \"label_positions\": 라벨 위치}\n"
        "• Assist: 넣지 않음",
        "SC, GSPS, SC Report, Encap PDF (CXR, Triage, Fundus)",
    ),
}


def tag_tooltip(group: str, element: str) -> str:
    """TAG_TOOLTIPS의 내용·범위를 툴팁용 rich text로 반환한다. 정의가 없으면 빈 문자열."""
    entry = TAG_TOOLTIPS.get(f"{group},{element}")
    if entry is None:
        return ""
    content, scope = (html.escape(s).replace("\n", "<br>") for s in entry)
    # rich text로 지정해야 긴 문구가 자동 줄바꿈된다
    return f"<qt>내용: {content}<br>범위: {scope}</qt>"
