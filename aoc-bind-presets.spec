# Build on Windows: python -m PyInstaller aoc-bind-presets.spec
from pathlib import Path
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

version = (Path(SPECPATH) / 'VERSION').read_text(encoding='utf-8').strip()
version_parts = tuple(int(part) for part in version.split('.'))
if len(version_parts) != 3 or any(part < 0 or part > 65535 for part in version_parts):
    raise ValueError('VERSION must contain a version such as 1.0.0.')
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_parts + (0,), prodvers=version_parts + (0,)),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('FileDescription', 'AoC-bind-presets'),
            StringStruct('FileVersion', version),
            StringStruct('InternalName', 'AoC-bind-presets'),
            StringStruct('OriginalFilename', 'AoC-bind-presets.exe'),
            StringStruct('ProductName', 'AoC-bind-presets'),
            StringStruct('ProductVersion', version),
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ],
)

a = Analysis(
    ['presets.py'],
    pathex=[],
    binaries=[],
    datas=[('icons', 'icons'), ('VERSION', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AoC-bind-presets',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=['icons/app.ico'],
    version=version_info,
)
