# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# pydicom 데이터(사전 등)와 JPEG 디코딩(pillow) 핸들러, IOD 허용 태그 목록, 창 아이콘 포함
datas = collect_data_files('pydicom')
datas += [('iod_tags.json', '.'), ('creator.ico', '.')]

a = Analysis(['app.py'],
             pathex=['.'],
             binaries=[],
             datas=datas,
             hiddenimports=collect_submodules('pydicom.pixels'),
             hookspath=[],
             hooksconfig={},
             runtime_hooks=[],
             excludes=['PyQt5', 'PyQt6', 'tkinter', 'numba', 'llvmlite', 'scipy', 'matplotlib', 'pandas',
                       'pyarrow', 'openpyxl', 'sqlalchemy', 'pytest', 'fsspec', 'lxml', 'gi', 'IPython',
                       'cryptography', 'win32com', 'pygments'],
             cipher=block_cipher,
             noarchive=False)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name='GatewayDcmCreator',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=False,
          disable_windowed_traceback=False,
          argv_emulation=False,
          target_arch=None,
          codesign_identity=None,
          entitlements_file=None,
          icon='creator.ico')
