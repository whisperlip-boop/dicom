"""DICOM 파일 수집·읽기·값 직렬화·역인덱스 생성을 수행하는 백그라운드 워커."""
import fnmatch
import logging
import os
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import pydicom
from pydicom.dataelem import DataElement
from pydicom.dataset import Dataset
from pydicom.multival import MultiValue
from PyQt5.QtCore import QThread, pyqtSignal

from app_paths import APP_NAME


logger = logging.getLogger(APP_NAME).getChild(__name__)

# 값 전체를 메모리에 두지 않고 존재·크기만 기록할 VR (UN은 내용을 보고 _format_un에서 판단)
_BINARY_VRS = frozenset({"OB", "OW", "OF", "OD", "OL", "OV"})
# UN 값을 텍스트로 볼 최대 길이(이보다 길면 바이너리로 취급)
_UN_TEXT_MAX_BYTES = 1024
_DCM_PATTERN = "*.dcm"
_SCAN_PROGRESS_INTERVAL_SEC = 0.25
_SCAN_CANCEL_CHECK_STEP = 100


def make_index_key(search_path: str, recursive: bool) -> tuple[str, bool]:
    """인덱스 재사용 판단용 키를 만든다.

    Args:
        search_path: 사용자가 입력한 검색 경로.
        recursive: 하위 폴더 포함 여부.

    Returns:
        (정규화된 절대경로, recursive).
    """
    return os.path.normcase(os.path.abspath(search_path)), recursive


def _is_hidden(name: str) -> bool:
    """glob 기본 규칙과 같게 '.'으로 시작하는 이름을 숨김으로 본다."""
    return name.startswith(".")


class IndexingWorker(QThread):
    """백그라운드에서 DICOM 파일을 탐색하고 인덱싱하는 워커 스레드.

    파일 탐색(Phase 1)과 인덱싱(Phase 2)을 순차 수행한다.
    탐색 단계에서는 total=0 신호로 indeterminate 진행 바를 표시하고,
    인덱싱 단계에서는 정확한 진행률을 전달한다.
    """

    # current: 처리 완료 수, total: 전체 수 (0이면 탐색 중 — indeterminate), message: 상태 텍스트
    progress = pyqtSignal(int, int, str)
    # QThread.finished(스레드 종료)를 가리지 않도록 이름을 indexed로 둔다
    indexed = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, search_path: str, recursive: bool, max_workers: int = 4) -> None:
        """워커를 초기화한다.

        Args:
            search_path: DICOM 파일을 탐색할 루트 경로.
            recursive: 하위 폴더 포함 여부.
            max_workers: 병렬 파일 읽기 스레드 수.
        """
        super().__init__()
        self.search_path = search_path
        self.recursive = recursive
        self.max_workers = max_workers
        self.index_key = make_index_key(search_path, recursive)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """취소를 요청한다. 워커 루프가 다음 확인 지점에서 조기 종료한다."""
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        """취소 요청 여부를 반환한다."""
        return self._cancel_event.is_set()

    def run(self) -> None:
        """파일 탐색 → 인덱싱 두 단계로 실행한다.

        탐색 단계에서는 total=0 신호로 indeterminate 진행 바를 표시하고,
        인덱싱 단계에서는 정확한 진행률을 전달한다.
        취소되면 indexed/error 신호 없이 종료한다.
        """
        try:
            self.progress.emit(0, 0, "Scanning for DICOM files...")
            files = self._collect_files()
            if files is None:
                logger.info("Indexing cancelled during file scan")
                return
            total_files = len(files)

            if total_files == 0:
                logger.info("Indexing complete: no DICOM files found")
                self.indexed.emit({
                    'dicom_index': {}, 'dicom_tag_values': {}, 'total_files': 0,
                    'failed_count': 0, 'failed_by_class': {},
                })
                return

            dicom_index: defaultdict[str, list[str]] = defaultdict(list)
            dicom_tag_values: dict[str, dict[str, list[str]]] = {}
            failed_by_class: Counter[str] = Counter()

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(self._read_dicom_file, file_path): file_path
                    for file_path in files
                }

                # 한계: as_completed와 with 종료 대기는 무기한이다. 실행 중인 dcmread(최대 max_workers개)는
                # 중단할 수 없어, 네트워크 드라이브 등에서 파일 하나가 멈추면 취소·창 닫기도 그만큼 기다린다.
                for idx, future in enumerate(as_completed(futures)):
                    if self.is_cancelled():
                        # 대기 중 작업은 취소, 실행 중 작업은 with 종료 시 완료를 기다린다
                        executor.shutdown(wait=False, cancel_futures=True)
                        logger.info("Indexing cancelled after %d / %d files", idx, total_files)
                        return

                    file_path = futures[future]
                    try:
                        file_tags = future.result()
                    except Exception as e:
                        failed_by_class[type(e).__name__] += 1
                        # 경로·예외 메시지에 환자정보가 있을 수 있어 경로는 DEBUG, 메시지는 기록하지 않음
                        logger.debug("Failed to read %s (%s)", file_path, type(e).__name__)
                    else:
                        dicom_tag_values[file_path] = file_tags
                        for tag in file_tags:
                            dicom_index[tag].append(file_path)

                    self.progress.emit(idx + 1, total_files, f"{idx + 1} / {total_files} files indexed")

            failed_count = sum(failed_by_class.values())
            if failed_count:
                logger.warning(
                    "Failed to read %d file(s): %s", failed_count,
                    ", ".join(f"{name}={count}" for name, count in sorted(failed_by_class.items()))
                )
            logger.info("Indexing complete: %d indexed, %d failed", len(dicom_tag_values), failed_count)

            self.indexed.emit({
                'dicom_index': dict(dicom_index),
                'dicom_tag_values': dicom_tag_values,
                'total_files': total_files,
                'failed_count': failed_count,
                'failed_by_class': dict(failed_by_class),
            })
        except Exception as e:
            logger.error("Indexing failed: %s", type(e).__name__)
            self.error.emit(f"인덱싱 중 오류 발생: {str(e)}")

    def _collect_files(self) -> list[str] | None:
        """검색 경로에서 .dcm 항목 목록을 수집한다.

        기존 glob.glob('**/*.dcm') 매칭 규칙을 그대로 따른다:
        '.'으로 시작하는 파일·폴더 제외, 대소문자는 OS 규칙(fnmatch), '*.dcm' 이름의 폴더도 포함,
        폴더 심볼릭 링크는 따라간다. .dcm이 없는 큰 트리에서도 바로 취소되도록
        폴더마다, 그리고 이름 100개마다 취소 여부를 확인한다.

        Returns:
            경로 목록. 취소되면 None.
        """
        files: list[str] = []
        dir_count = 0
        walk_errors: Counter[str] = Counter()

        def on_walk_error(err: OSError) -> None:
            walk_errors[type(err).__name__] += 1
            # 경로에 환자정보가 있을 수 있어 DEBUG로만 기록
            logger.debug("Cannot list %s (%s)", err.filename, type(err).__name__)

        # glob과 같은 경로 문자열이 나오도록 끝 구분자를 뗀다(드라이브·UNC 루트 'C:\'는 유지)
        drive, rest = os.path.splitdrive(self.search_path)
        top = drive + rest.rstrip("\\/") if rest.rstrip("\\/") else self.search_path

        last_emit = time.monotonic()
        for dirpath, dirnames, filenames in os.walk(top, onerror=on_walk_error, followlinks=True):
            if self.is_cancelled():
                return None
            dir_count += 1
            dirnames[:] = [d for d in dirnames if not _is_hidden(d)]
            names = [f for f in filenames if not _is_hidden(f)] + dirnames
            for i, name in enumerate(fnmatch.filter(names, _DCM_PATTERN)):
                if i % _SCAN_CANCEL_CHECK_STEP == 0 and self.is_cancelled():
                    return None
                files.append(os.path.join(dirpath, name))
            if not self.recursive:
                dirnames[:] = []
            now = time.monotonic()
            if now - last_emit >= _SCAN_PROGRESS_INTERVAL_SEC:
                last_emit = now
                self.progress.emit(
                    0, 0, f"Scanning for DICOM files... {dir_count} folders, {len(files)} files found"
                )

        if walk_errors:
            logger.warning(
                "Could not list %d folder(s) during scan: %s", sum(walk_errors.values()),
                ", ".join(f"{name}={count}" for name, count in sorted(walk_errors.items()))
            )
        return files

    def _read_dicom_file(self, file_path: str) -> dict[str, list[str]]:
        """단일 DICOM 파일을 읽고 태그 정보를 반환한다.

        시퀀스(SQ) 내부 데이터셋의 요소도 재귀적으로 포함한다.

        Args:
            file_path: 읽을 DICOM 파일의 절대 경로.

        Returns:
            태그 키('GGGG,EEEE')와 값 목록의 딕셔너리. 이미 취소됐으면 읽지 않고 빈 딕셔너리
            (취소 후 결과는 run()에서 버려진다).

        Raises:
            Exception: 파일 읽기·파싱 실패 시 pydicom/OS 예외를 그대로 전달한다(호출부에서 집계).
        """
        if self.is_cancelled():
            return {}
        ds = pydicom.dcmread(file_path, stop_before_pixels=True)
        file_tags: dict[str, list[str]] = {}
        self._collect_elements(ds, file_tags)
        return file_tags

    @classmethod
    def _collect_elements(cls, ds: Dataset, file_tags: dict[str, list[str]]) -> None:
        """데이터셋의 요소 값을 file_tags에 누적한다(SQ 항목은 재귀)."""
        for elem in ds:
            tag = elem.tag
            full_tag = f"{tag.group:04X},{tag.element:04X}"
            file_tags.setdefault(full_tag, []).append(cls._format_value(elem))
            if elem.VR == "SQ" and elem.value:
                for item in elem.value:
                    cls._collect_elements(item, file_tags)

    @staticmethod
    def _format_value(elem: DataElement) -> str:
        """요소 값을 검색용 문자열로 변환한다.

        Args:
            elem: 변환할 DICOM 데이터 요소.

        Returns:
            MultiValue는 DICOM 표기('\\' 구분), SQ는 'SQ[N items]',
            바이너리 VR은 '<binary N bytes>', UN은 _format_un 규칙, 그 외는 str(value).
        """
        value = elem.value
        vr = str(elem.VR or "")
        if vr == "UN":
            return IndexingWorker._format_un(value)
        # "OB or OW" 같은 모호 VR도 바이너리로 취급
        if any(part.strip() in _BINARY_VRS for part in vr.split(" or ")):
            try:
                length = len(value) if value is not None else 0
            except TypeError:
                length = 0
            return f"<binary {length} bytes>"
        if value is None:
            return ""
        if vr == "SQ":
            return f"SQ[{len(value)} items]"
        if isinstance(value, MultiValue):
            return "\\".join(str(v) for v in value)
        return str(value)

    @staticmethod
    def _format_un(value: object) -> str:
        """UN 값을 텍스트 또는 '<binary N bytes>'로 변환한다.

        Implicit VR 파일의 private 문자열 태그가 UN으로 읽혀도 검색되도록, 1024바이트 이하이고
        latin-1 디코딩 후 끝의 NUL·공백을 뗀 결과가 모두 출력 가능 문자면 텍스트로 본다.

        Args:
            value: UN 요소의 값(보통 bytes).

        Returns:
            텍스트 값 또는 '<binary N bytes>'.
        """
        if value is None:
            return ""
        if isinstance(value, (bytes, bytearray)):
            if len(value) <= _UN_TEXT_MAX_BYTES:
                text = bytes(value).decode("latin-1").rstrip("\x00 ")
                if text.isprintable():
                    return text
            return f"<binary {len(value)} bytes>"
        try:
            return f"<binary {len(value)} bytes>"
        except TypeError:
            return "<binary 0 bytes>"
