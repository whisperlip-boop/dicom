"""DICOM 태그 정밀 검색기 진입점: 로깅 설정 후 메인 창을 띄운다."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon

from app_paths import APP_NAME, LOG_FILE_NAME, get_output_dirs, get_resource_path
from ui import DICOMTagSearcher


_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUP_COUNT = 2


def setup_logging() -> None:
    """앱 로거('dicom_tag_finder')에 회전 파일 핸들러를 1회 연결한다.

    로그 파일은 프로그램 폴더에 두고, 쓸 수 없으면 LOCALAPPDATA 폴더로 폴백한다.
    루트 로거가 아닌 앱 로거에만 연결해, pydicom 등 외부 라이브러리 경고(태그 값 포함 가능)가
    로그 파일에 섞이지 않게 한다. 모든 후보에서 실패하면 파일 로그 없이 계속 실행한다.
    """
    app_logger = logging.getLogger(APP_NAME)
    if app_logger.handlers:
        return
    # 환자정보 노출 방지: 기본 INFO. 실패 파일 경로는 DEBUG로만 기록되므로 기본값에선 남지 않는다
    app_logger.setLevel(logging.INFO)
    failed_dirs: list[tuple[str, str]] = []
    for log_dir in get_output_dirs():
        try:
            os.makedirs(log_dir, exist_ok=True)
            handler = RotatingFileHandler(
                os.path.join(log_dir, LOG_FILE_NAME),
                maxBytes=_LOG_MAX_BYTES,
                backupCount=_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
        except OSError as e:
            failed_dirs.append((log_dir, type(e).__name__))
            continue
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        app_logger.addHandler(handler)
        for failed_dir, error_name in failed_dirs:
            app_logger.warning("Cannot write log to %s (%s); using %s", failed_dir, error_name, log_dir)
        return
    app_logger.addHandler(logging.NullHandler())


def main() -> int:
    """애플리케이션을 실행한다.

    Returns:
        Qt 이벤트 루프 종료 코드.
    """
    setup_logging()
    app = QApplication(sys.argv)
    icon_path = get_resource_path("searcher.ico")
    app.setWindowIcon(QIcon(icon_path))
    window = DICOMTagSearcher()
    window.setWindowIcon(QIcon(icon_path))
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
