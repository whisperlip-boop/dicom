# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

from PyInstaller.utils.hooks import collect_data_files
import matplotlib

# matplotlib 리소스 포함
matplotlib_data = collect_data_files('matplotlib')

a = Analysis(
    ['gsps_viewer.py'],
    pathex=[],
    binaries=[],
    datas=matplotlib_data + [('document-viewer.ico', '.')],
    hiddenimports=[
        'matplotlib.backends.backend_qt5agg',
        'matplotlib.pyplot',
        'PyQt5.QtWidgets',
        'PyQt5.QtGui',
        'PyQt5.QtCore'
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='gsps_viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=['D:\\github\\dicom\\gsps\\document-viewer.ico'],
    onefile=True
)

