"""dicom-validator의 표준 JSON에서 IOD별 허용 태그 목록(iod_tags.json)을 만든다.

사용법: python tools/gen_iod_tags.py <dicom-validator standard json 폴더>
예:     python tools/gen_iod_tags.py %USERPROFILE%/dicom-validator/2026d/json
"""
import json
import os
import sys

SOP_CLASSES = {
    "CR": "1.2.840.10008.5.1.4.1.1.1",
    "DX": "1.2.840.10008.5.1.4.1.1.1.1",
    "XC": "1.2.840.10008.5.1.4.1.1.77.1.4",
    "OT": "1.2.840.10008.5.1.4.1.1.7",
}


def module_tags(modules: dict, ref: str, seen: set[str]) -> set[str]:
    """모듈(또는 매크로) 최상위 태그를 include까지 풀어서 모은다."""
    if ref in seen or ref not in modules:
        return set()
    seen.add(ref)
    tags: set[str] = set()
    for key, value in modules[ref].items():
        if key == "include":
            for inc in value:
                tags |= module_tags(modules, inc["ref"], seen)
        elif key.startswith("("):
            tags.add(key[1:10].replace(",", ""))
    return tags


def main() -> None:
    src = sys.argv[1]
    with open(os.path.join(src, "iod_info.json"), encoding="utf-8") as f:
        iods = json.load(f)
    with open(os.path.join(src, "module_info.json"), encoding="utf-8") as f:
        modules = json.load(f)
    with open(os.path.join(src, "version"), encoding="utf-8") as f:
        version = f.read().strip()

    result: dict = {"dicom_revision": version, "iods": {}}
    for modality, uid in SOP_CLASSES.items():
        tags: set[str] = set()
        for mod in iods[uid]["modules"].values():
            tags |= module_tags(modules, mod["ref"], set())
        result["iods"][modality] = sorted(tags)

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "iod_tags.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    for modality, tags in result["iods"].items():
        print(modality, len(tags))


if __name__ == "__main__":
    main()
