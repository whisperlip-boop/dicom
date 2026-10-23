# DICOM_tag_sercher.spec
# 경로는 spec 파일 위치(SPECPATH) 기준이라 어느 폴더에서 빌드해도 동일하게 동작한다
import os

block_cipher = None

a = Analysis(
    [os.path.join(SPECPATH, 'dicom_tag_finder.py')],
    pathex=[SPECPATH],
    binaries=[],
    datas=[(os.path.join(SPECPATH, 'searcher.ico'), '.')],
    # 로컬 모듈·PyQt5는 정적 import라 자동 수집된다
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # 앱은 PyQt5 + pydicom(태그만, stop_before_pixels)만 사용한다.
    # 아래는 pydicom의 선택적 import(try/except)를 통해 딸려 오던 패키지라 제외해도 동작이 같다.
    excludes=[
        # pydicom.data.download → tqdm/requests → pandas·pyarrow·scipy·numba·matplotlib 등 연쇄 유입
        'tqdm',
        'requests',
        # pydicom 픽셀 디코더 백엔드(픽셀 데이터는 읽지 않음)
        'numpy',
        'PIL',
        'gdcm',
        '_gdcm',
        'pylibjpeg',
        'openjpeg',
        'libjpeg',
        # 위 연쇄로 들어오던 대형 패키지(다른 경로 유입 방지용)
        'pandas',
        'pyarrow',
        'scipy',
        'numba',
        'llvmlite',
        'matplotlib',
        'PySide6',
        'shiboken6',
        'IPython',
        'jinja2',
        'lxml',
        'sqlalchemy',
        'cryptography',
        'aiohttp',
        'fsspec',
        'pytest',
        '_pytest',
        'pygments',
        'setuptools',
        'pkg_resources',
        'openpyxl',
        'xlsxwriter',
        'tables',
        'numexpr',
        'pytz',
        'brotli',
        '_brotli',
        'psutil',
        'pysqlite2',
        'MySQLdb',
        'psycopg2',
        'tbb',
        'python38.dll'
    ],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

coll = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DICOM_tag_sercher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=os.path.join(SPECPATH, 'searcher.ico')
)
