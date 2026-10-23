"""dicom_tag_finder 공용 경로·이름 상수.

진입점·UI·인덱서가 함께 쓰는 경로 함수를 한곳에 두어 순환 import를 피한다.
"""
import os
import sys


APP_NAME = "dicom_tag_finder"
LOG_FILE_NAME = "dicom_tag_finder.log"
RESULTS_FILE_NAME = "dicom_search_results.txt"


def get_resource_path(relative_path: str) -> str:
    """스크립트 위치를 기준으로 리소스 파일의 절대 경로를 반환한다.

    PyInstaller 빌드 시에는 sys._MEIPASS, 일반 실행 시에는 스크립트 디렉토리를 기준으로 한다.

    Args:
        relative_path: 애플리케이션 루트 기준 상대 경로.

    Returns:
        리소스 파일의 절대 경로.
    """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def get_program_dir() -> str:
    """프로그램이 있는 폴더를 반환한다(CWD와 무관).

    Returns:
        PyInstaller 빌드면 exe 폴더, 스크립트 실행이면 이 모듈이 있는 폴더의 절대 경로.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_app_data_dir() -> str:
    """폴백 폴더(%LOCALAPPDATA%\\dicom_tag_finder) 경로를 반환한다.

    LOCALAPPDATA가 없으면 사용자 홈 폴더를 기준으로 한다. 폴더는 생성하지 않는다.

    Returns:
        폴백 폴더의 절대 경로.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def get_output_dirs() -> list[str]:
    """결과·로그 파일을 쓸 폴더 후보를 우선순위대로 반환한다.

    Returns:
        [프로그램 폴더, LOCALAPPDATA 폴백 폴더].
    """
    return [get_program_dir(), get_app_data_dir()]
