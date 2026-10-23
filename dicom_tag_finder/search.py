"""태그 조건 모델과 And/Or(DNF) 집합 검색. Qt에 의존하지 않는 순수 함수 모음."""
from typing import NamedTuple


class TagCondition(NamedTuple):
    """검색 조건 1행.

    Attributes:
        tag: 'GGGG,EEEE' 형식의 태그 키(대문자 16진수).
        value: 검색 값. 빈 문자열이면 태그 존재 여부만 확인한다.
        is_or: True이면 이 행부터 새 OR 그룹을 시작한다.
    """

    tag: str
    value: str
    is_or: bool


def make_tag_key(group: str, element: str) -> str | None:
    """Group·Element 입력을 'GGGG,EEEE' 키로 만든다.

    Args:
        group: Group 입력 문자열.
        element: Element 입력 문자열.

    Returns:
        둘 다 4자리이면 대문자 태그 키, 아니면 None.
    """
    g = group.strip().upper()
    e = element.strip().upper()
    if g and e and len(g) == 4 and len(e) == 4:
        return f"{g},{e}"
    return None


def value_matches(stored_value: str, query_value: str) -> bool:
    """SQL LIKE 스타일 와일드카드 비교를 수행한다.

    Args:
        stored_value: 인덱스에 저장된 태그 값.
        query_value: 검색 입력값. '*'를 와일드카드로 사용한다.

    Returns:
        값이 조건에 부합하면 True.

    Examples:
        *abc  → endswith 'abc'
        abc*  → startswith 'abc'
        *abc* → contains 'abc'
        abc   → exact match
    """
    if not query_value:
        return True

    if query_value.startswith("*") and query_value.endswith("*"):
        return query_value.strip("*") in stored_value

    if query_value.startswith("*"):
        return stored_value.endswith(query_value.lstrip("*"))

    if query_value.endswith("*"):
        return stored_value.startswith(query_value.rstrip("*"))

    return stored_value == query_value


def split_groups(conditions: list[TagCondition]) -> list[list[tuple[str, str]]]:
    """Or 위치에서 조건을 AND 그룹으로 나눈다.

    예) Row1, Row2[And], Row3[And], Row4[Or], Row5[And]
        → [[Row1, Row2, Row3], [Row4, Row5]]

    Args:
        conditions: 입력 순서대로의 조건 목록.

    Returns:
        (tag, value) 목록의 그룹 목록.
    """
    groups: list[list[tuple[str, str]]] = []
    current_group: list[tuple[str, str]] = []
    for tag, value, is_or in conditions:
        if is_or and current_group:
            groups.append(current_group)
            current_group = []
        current_group.append((tag, value))
    if current_group:
        groups.append(current_group)
    return groups


def search_files(
    groups: list[list[tuple[str, str]]],
    dicom_index: dict[str, list[str]],
    dicom_tag_values: dict[str, dict[str, list[str]]],
    all_files: list[str],
) -> set[str]:
    """각 AND 그룹의 교집합을 구한 뒤 모든 그룹의 합집합을 반환한다.

    Args:
        groups: split_groups 결과.
        dicom_index: 태그 → 파일 경로 목록(역인덱스).
        dicom_tag_values: 파일 경로 → 태그 → 값 목록.
        all_files: 인덱싱된 전체 파일 경로.

    Returns:
        조건을 만족하는 파일 경로 집합.
    """
    final_results: set[str] = set()
    for group in groups:
        # AND 최적화: 후보 파일이 가장 적은 태그부터 처리
        group_sorted = sorted(group, key=lambda x: len(dicom_index.get(x[0], [])))
        group_results: set[str] = set(all_files)
        for tag, value in group_sorted:
            # 한 파일에 같은 태그가 여러 번(SQ 중첩) 있으면 하나라도 맞으면 일치
            matching: set[str] = {
                fp for fp in dicom_index.get(tag, [])
                if not value or any(
                    value_matches(stored, value)
                    for stored in dicom_tag_values.get(fp, {}).get(tag, [])
                )
            }
            group_results &= matching
            if not group_results:
                break
        final_results |= group_results
    return final_results


def describe_groups(groups: list[list[tuple[str, str]]]) -> str:
    """그룹 조건을 사람이 읽을 수 있는 식으로 만든다.

    Args:
        groups: split_groups 결과(1개 이상).

    Returns:
        예: '(0008,0008=A AND 0010,0010) OR (0020,000D)'.
    """
    group_strs = [
        " AND ".join(f"{t}={v}" if v else t for t, v in g)
        for g in groups
    ]
    if len(groups) > 1:
        return " OR ".join(f"({s})" for s in group_strs)
    return group_strs[0]
