# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Collect all data files needed by pydicom, gdcm, and pylibjpeg
datas = collect_data_files('pydicom')
datas += collect_data_files('gdcm')
datas += collect_data_files('pylibjpeg')
datas += collect_data_files('pylibjpeg-libjpeg')
datas += collect_data_files('pylibjpeg-openjpeg')
datas += [('editor.ico', '.')]

a = Analysis(['app.py'],
             pathex=[],
             binaries=[],
             datas=datas,
             hiddenimports=collect_submodules('pydicom') +
                           collect_submodules('gdcm') +
                           collect_submodules('pylibjpeg'),
             hookspath=[],
             hooksconfig={},
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          exclude_binaries=False,
          name='DICOMTagEditor',
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
          icon=['editor.ico'])
