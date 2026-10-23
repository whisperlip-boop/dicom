"""폴더 스캔 → Study 단위 값 생성 → 파일별 태그 적용·저장.

로그에는 파일 경로와 처리 결과만 남기고 환자정보(이름, PID 등)는 남기지 않는다.
"""
import logging
import os
import random
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

import pydicom
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

import rules
from iod import IncompatibleImage, apply_iod, can_convert, convert_pixels, incompatibility

logger = logging.getLogger(__name__)


@dataclass
class Entry:
    path: str
    rel: str
    header: Dataset


@dataclass
class GroupValues:
    """같은 Study Instance UID로 묶인 파일들이 함께 쓰는 값."""
    study_date: str = ""
    series_date: str = ""
    study_time: str = ""
    series_time: str = ""
    accession: str = ""
    drid: str = ""
    preg: str = ""
    name: str = ""
    patient_id: str = ""
    birth_date: str = ""
    sex: str = ""
    modality: str | None = None
    skip_reason: str | None = None
    study_uid: str = ""
    series_uids: dict[str, str] = field(default_factory=dict)


@dataclass
class Summary:
    total: int = 0
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False


LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int], None]


def same_dir(a: str, b: str) -> bool:
    """두 경로가 같은 폴더를 가리키는지(대소문자·상대경로 무시)."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _is_under(path: str, parent: str) -> bool:
    path, parent = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(parent))
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:  # 서로 다른 드라이브
        return False


def scan_files(input_dir: str, recursive: bool, output_dir: str) -> list[str]:
    """.dcm 파일 목록. 출력 폴더가 입력 폴더 안에 있으면 그 아래는 제외한다."""
    exclude = None if same_dir(input_dir, output_dir) or not _is_under(output_dir, input_dir) else output_dir
    found: list[str] = []
    for root, dirs, files in os.walk(input_dir):
        if exclude:
            dirs[:] = [d for d in dirs if not same_dir(os.path.join(root, d), exclude)]
        found += [os.path.join(root, f) for f in files if f.lower().endswith(".dcm")]
        if not recursive:
            break
    return sorted(found)


def _safe_error(e: Exception) -> str:
    """예외 메시지에 태그 값(환자정보)이 섞일 수 있어 종류만 남긴다. OS 오류는 사유까지."""
    if isinstance(e, OSError) and e.strerror:
        return f"{type(e).__name__}: {e.strerror}"
    return type(e).__name__


def safe_traceback(e: BaseException) -> str:
    """예외 메시지 없이 호출 스택(파일·라인·소스)만. 로그 파일에 환자정보가 남지 않게 한다."""
    return "".join(traceback.format_tb(e.__traceback__))


class Processor:
    def __init__(self, settings: dict, log: LogFn, progress: ProgressFn,
                 is_cancelled: Callable[[], bool] = lambda: False, seed: int | None = None) -> None:
        self.s = settings
        self.on = settings["enabled"]
        self.emit = log
        self.progress = progress
        self.is_cancelled = is_cancelled
        self.rng = random.Random(seed)
        self.summary = Summary()  # 예외로 중단돼도 그때까지의 집계를 남긴다

    def log(self, message: str, level: int = logging.INFO) -> None:
        """GUI 콜백과 로그 파일 양쪽에 남긴다."""
        logger.log(level, message)
        self.emit(message)

    # ------------------------------------------------------------ 값 생성
    def _pick_modality(self, entries: list[Entry]) -> tuple[str | None, str | None]:
        checked = [m for m in rules.MODALITIES if self.s["modality"][m]]
        if not self.on["sop_class"]:
            return self.rng.choice(checked), None
        convert = self.s["convert_pixels"]
        usable: list[str] = []
        reasons: set[str] = set()  # 모두 불일치일 때 사유로 보여주므로 한 번의 순회에서 함께 모은다
        for m in checked:
            ok = True
            for e in entries:
                reason = incompatibility(m, e.header)
                if reason is None:
                    continue
                reasons.add(reason)
                if not (convert and can_convert(e.header)):
                    ok = False
            if ok:
                usable.append(m)
        if not usable:
            return None, "선택한 Modality 모두 영상 형식 불일치: " + "; ".join(sorted(reasons))
        return self.rng.choice(usable), None

    def _make_groups(self, entries: list[Entry]) -> dict[str, tuple[list[Entry], GroupValues]]:
        grouped: dict[str, list[Entry]] = {}
        for e in entries:
            key = str(e.header.get("StudyInstanceUID", "")) or f"__file__:{e.path}"
            grouped.setdefault(key, []).append(e)

        n = len(grouped)
        s, rng = self.s, self.rng
        accessions = rules.unique_values(rng, n, rules.random_accession) if self.on["accession"] else []
        pids = rules.random_patient_ids(rng, n) if self.on["patient_id"] else []
        drids = rules.balanced_choices(rng, rules.parse_drid(s["drid"]), n) if self.on["image_comments"] else []
        pregs = [p for p in rules.PREG_OPTIONS if s["preg"][p]]
        sexes = [x for x in rules.SEXES if s["sex"][x]]

        result: dict[str, tuple[list[Entry], GroupValues]] = {}
        for i, (key, group) in enumerate(grouped.items()):
            v = GroupValues()
            if self.on["study_date"]:
                v.study_date = rules.random_date(rng, s["study_date"]["start"], s["study_date"]["end"])
            if self.on["series_date"] and not s["series_date"]["same"]:
                v.series_date = rules.random_date(rng, s["series_date"]["start"], s["series_date"]["end"])
            if self.on["study_time"]:
                v.study_time = rules.random_time(rng, s["study_time"]["start"], s["study_time"]["end"])
            if self.on["series_time"] and not s["series_time"]["same"]:
                v.series_time = rules.random_time(rng, s["series_time"]["start"], s["series_time"]["end"])
            if self.on["accession"]:
                v.accession = accessions[i]
            if self.on["image_comments"]:
                v.drid, v.preg = drids[i], rng.choice(pregs)
            if self.on["sex"]:
                v.sex = rng.choice(sexes)
            if self.on["patient_name"]:
                sex = v.sex if self.on["sex"] else str(group[0].header.get("PatientSex", ""))
                v.name = rules.random_name(rng, sex)
            if self.on["patient_id"]:
                v.patient_id = pids[i]
            if self.on["birth_date"]:
                v.birth_date = rules.random_date(rng, s["birth_date"]["start"], s["birth_date"]["end"])
            if self.on["modality"]:
                try:
                    v.modality, v.skip_reason = self._pick_modality(group)
                except Exception as e:  # 헤더 하나가 깨져도 해당 Study만 건너뛰고 나머지는 진행
                    v.skip_reason = f"헤더 판독 실패 ({_safe_error(e)})"
            if s["regenerate_uids"]:
                v.study_uid = generate_uid()
            result[key] = (group, v)
        return result

    # ------------------------------------------------------------ 파일 적용
    def _apply(self, ds: Dataset, v: GroupValues) -> list[str]:
        """태그를 적용하고 경고 목록을 돌려준다. 건너뛰어야 하면 IncompatibleImage."""
        on, s = self.on, self.s
        warnings: list[str] = []

        if on["study_date"]:
            ds.StudyDate = v.study_date
        if on["series_date"]:
            if not s["series_date"]["same"]:
                ds.SeriesDate = v.series_date
            elif ds.get("StudyDate"):  # Study Date가 없으면 기존 Series Date 유지
                ds.SeriesDate = str(ds.StudyDate)
        if on["study_time"]:
            ds.StudyTime = v.study_time
        if on["series_time"]:
            if not s["series_time"]["same"]:
                ds.SeriesTime = v.series_time
            elif ds.get("StudyTime"):
                ds.SeriesTime = str(ds.StudyTime)
        if on["accession"]:
            ds.AccessionNumber = v.accession
        if on["patient_name"]:
            ds.PatientName = v.name
        if on["patient_id"]:
            ds.PatientID = v.patient_id
        if on["birth_date"]:
            ds.PatientBirthDate = v.birth_date
        if on["sex"]:
            ds.PatientSex = v.sex
        if on["age"]:
            age = rules.calc_age(str(ds.get("PatientBirthDate", "")), str(ds.get("StudyDate", "")))
            if age:
                ds.PatientAge = age
            else:
                warnings.append("Patient's Age 계산 불가(생년월일/Study Date 없음 또는 음수) → 건너뜀")
        if on["image_comments"]:
            ds.ImageComments = rules.build_image_comments(
                v.drid, v.preg, str(ds.get("StudyDate", "")), str(ds.get("StudyTime", "")))

        if s["regenerate_uids"]:
            old_series = str(ds.get("SeriesInstanceUID", ""))
            ds.StudyInstanceUID = v.study_uid
            if old_series not in v.series_uids:
                v.series_uids[old_series] = generate_uid()
            ds.SeriesInstanceUID = v.series_uids[old_series]
            ds.SOPInstanceUID = generate_uid()
            if hasattr(ds, "file_meta"):
                ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

        target = v.modality if on["modality"] else str(ds.get("Modality", ""))
        if on["modality"]:
            ds.Modality = target
        if on["sop_class"]:
            if target not in rules.SOP_CLASS_BY_MODALITY:
                warnings.append(f"Modality '{target}'에 대응하는 SOP Class가 없어 SOP Class UID는 건너뜀")
            else:
                if "PixelData" not in ds:
                    raise IncompatibleImage("Pixel Data 없음")
                reason = incompatibility(target, ds)
                if reason:
                    if not (s["convert_pixels"] and can_convert(ds)):
                        raise IncompatibleImage(reason)
                    convert_pixels(ds, target)
                    warnings.append(f"영상 변환함 ({reason})")
                apply_iod(ds, target)
        return warnings

    def _output_path(self, rel: str) -> str:
        out = os.path.abspath(os.path.join(self.s["output_dir"], rel))
        if not _is_under(out, self.s["output_dir"]):  # 경로 조작 방지
            raise OSError(0, "출력 경로가 출력 폴더를 벗어남")
        return out

    def run(self) -> Summary:
        s = self.s
        input_dir, output_dir = s["input_dir"], s["output_dir"]
        paths = scan_files(input_dir, s["include_subfolders"], output_dir)
        summary = self.summary
        summary.total = len(paths)
        self.log(f"대상 파일 {len(paths)}개")
        if not paths:
            return summary

        entries: list[Entry] = []
        for i, path in enumerate(paths, 1):
            rel = os.path.relpath(path, input_dir)
            try:
                entries.append(Entry(path, rel, pydicom.dcmread(path, stop_before_pixels=True)))
            except Exception as e:
                summary.failed += 1
                self.log(f"[FAIL] {rel} — 읽기 실패 ({_safe_error(e)})", logging.WARNING)
            if self.is_cancelled():
                summary.cancelled = True
                return summary
        groups = self._make_groups(entries)
        self.log(f"Study {len(groups)}개로 묶음")

        done = summary.failed
        self.progress(done, summary.total)
        for group, v in groups.values():
            for e in group:
                if self.is_cancelled():
                    summary.cancelled = True
                    self.log("사용자가 중지했습니다.")
                    return summary
                tmp = None
                try:
                    if v.skip_reason:
                        raise IncompatibleImage(v.skip_reason)
                    ds = pydicom.dcmread(e.path)
                    warnings = self._apply(ds, v)
                    out = self._output_path(e.rel)
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    tmp = out + ".tmp"
                    ds.save_as(tmp)
                    os.replace(tmp, out)
                    summary.ok += 1
                    self.log(f"[OK] {e.rel}" + "".join(f"\n      - {w}" for w in warnings))
                except IncompatibleImage as ex:
                    summary.skipped += 1
                    self.log(f"[SKIP] {e.rel} — {ex}")
                except Exception as ex:
                    summary.failed += 1
                    self.log(f"[FAIL] {e.rel} — {_safe_error(ex)}", logging.WARNING)
                    logger.warning("traceback:\n%s", safe_traceback(ex))
                    if tmp and os.path.exists(tmp):
                        os.remove(tmp)
                done += 1
                self.progress(done, summary.total)
        return summary
