import os
import queue

import pydicom
from pydicom.dataelem import DataElement
from pydicom.tag import Tag

from utils import convert_value_for_vr


def process_dicom_worker(
    files_to_process: list[str],
    edit_tags: list[tuple[int, int, str, str]],
    del_tags: list[tuple[int, int]],
    output_folder: str,
    progress_queue: queue.Queue,
) -> None:
    """Worker thread that applies tag edits and deletions to DICOM files."""
    total_files = len(files_to_process)
    processed_files = 0
    failed_files = 0

    for dicom_path in files_to_process:
        filename = os.path.basename(dicom_path)
        file_failed = False

        try:
            dicom = pydicom.dcmread(dicom_path)
        except Exception as e:
            progress_queue.put(("error", f"Error reading DICOM file {filename}: {e}"))
            file_failed = True
            processed_files += 1
            failed_files += 1
            progress_queue.put(("progress", processed_files, total_files))
            continue

        for group, element, vr, new_value in edit_tags:
            tag = Tag(group, element)
            try:
                if tag == Tag(0x0010, 0x0010):  # Patient's Name: set charset for CJK compatibility
                    dicom.SpecificCharacterSet = ['ISO 2022 IR 100', 'ISO 2022 IR 13', 'ISO 2022 IR 87']
                if tag in dicom:
                    dicom[tag].value = convert_value_for_vr(dicom[tag].VR, new_value)
                else:
                    if vr == "Same":
                        progress_queue.put(("error", f"Tag {tag} does not exist and VR is not specified."))
                        file_failed = True
                        continue
                    converted_value = convert_value_for_vr(vr, new_value)
                    elem = DataElement(tag, vr, converted_value)
                    dicom.add(elem)
            except Exception as e:
                progress_queue.put(("error", f"Error editing tag {tag} in {filename}: {e}"))
                file_failed = True

        for group, element in del_tags:
            tag = Tag(group, element)
            try:
                if tag in dicom:
                    del dicom[tag]
                else:
                    progress_queue.put(("error", f"Tag {tag} not found in {filename}, skipping deletion."))
            except Exception as e:
                progress_queue.put(("error", f"Error deleting tag {tag} from {filename}: {e}"))
                file_failed = True

        output_path = os.path.join(output_folder, filename)
        try:
            dicom.save_as(output_path)
        except Exception as e:
            progress_queue.put(("error", f"Error saving {filename}: {e}"))
            file_failed = True

        processed_files += 1
        if file_failed:
            failed_files += 1
        progress_queue.put(("progress", processed_files, total_files))

    progress_queue.put(("done", failed_files, total_files))
