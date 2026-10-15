"""리소스 경로·pydicom 사전 확장 유틸리티."""
import os
import sys

from pydicom.datadict import add_private_dict_entries


def get_resource_path(relative_path: str) -> str:
    """PyInstaller 번들 및 개발 환경 모두에서 리소스 절대 경로를 반환한다.

    Args:
        relative_path: 리소스 파일의 상대 경로.

    Returns:
        실행 환경에 맞게 계산된 절대 경로 문자열.
    """
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def extend_dicom_dictionary() -> None:
    """pydicom 사전에 VUNO 전용 Private Tag 항목을 등록한다.

    그룹 0x1001 범위의 Private Tag를 'VUNO Private Tags' 블록 오너로
    등록하여 pydicom이 해당 태그를 인식할 수 있도록 한다.
    """
    private_entries: dict[tuple[int, int], tuple[str, str, str, str]] = {
        (0x1001, 0x1002): ('CS', '1', 'Private Version', ''),
        (0x1001, 0x1004): ('CS', '1', 'Private Flag 0/1', ''),
        (0x1001, 0x1005): ('CS', '1', 'Private Flag 1', ''),
        (0x1001, 0x1008): ('UT', '1', 'Private Report', ''),
        (0x1001, 0x1009): ('CS', '1', 'Private Type', ''),
        (0x1001, 0x1011): ('UT', '1', 'Private Report/RADS', ''),
        (0x1001, 0x1015): ('UT', '1', 'Private JSON', ''),
    }
    tag_dict = {
        (group << 16) | element: entry
        for (group, element), entry in private_entries.items()
    }
    add_private_dict_entries('VUNO Private Tags', tag_dict)
