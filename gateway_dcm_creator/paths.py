"""리소스·설정 파일 경로."""
import os
import sys
import uuid


def _is_writable(directory: str) -> bool:
    """실제로 파일을 만들어 본다. Windows의 os.access(W_OK)는 ACL을 보지 않아 Program Files도 True가 된다.

    tempfile.NamedTemporaryFile은 권한 없는 폴더에서 멈추는 경우가 있어 plain open을 쓴다.
    """
    probe = os.path.join(directory, f".write_test_{uuid.uuid4().hex}")
    try:
        open(probe, "x").close()
        os.remove(probe)
    except OSError:
        return False
    return True


def get_resource_path(relative_path: str) -> str:
    """PyInstaller로 묶였을 때와 소스로 실행할 때 모두 리소스 파일 경로를 찾는다."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def get_settings_path() -> str:
    """exe(또는 app.py) 옆의 settings.json. 쓸 수 없는 위치면 %APPDATA% 아래로."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    if _is_writable(base):
        return os.path.join(base, "settings.json")
    fallback = os.path.join(os.environ.get("APPDATA", base), "GatewayDcmCreator")
    os.makedirs(fallback, exist_ok=True)
    return os.path.join(fallback, "settings.json")
