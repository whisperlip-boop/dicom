"""VUNO DICOM 결과 태그 검증 도구.

원본 DICOM을 분석하여 재생성한 결과 DICOM의 태그 값이 사내 규칙에 따라
올바르게 들어 있는지 확인하는 PyQt5 GUI 애플리케이션.
"""
import sys

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon

from app import TagCheckerApp
from utils import extend_dicom_dictionary, get_resource_path


def main() -> None:
    """Private Tag 사전을 등록하고 메인 윈도우를 실행한다."""
    extend_dicom_dictionary()
    app = QApplication(sys.argv)
    icon_path = get_resource_path("checker.ico")
    app.setWindowIcon(QIcon(icon_path))
    window = TagCheckerApp()
    window.setWindowIcon(QIcon(icon_path))
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
