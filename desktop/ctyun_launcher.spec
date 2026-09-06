# -*- mode: python ; coding: utf-8 -*-
# CtYun 妗岄潰鐗堝惎鍔ㄥ櫒鎵撳寘閰嶇疆
# 璁捐锛歟xe 浠呭惈 launcher.py + paths.py锛堢函鏍囧噯搴擄級锛岄潰鏉?浠诲姟鑴氭湰鐢遍殢鍖?# runtime\python.exe 杩愯锛堣 launcher.py PYTHON_EXE 娉ㄩ噴锛夈€?# 鎺у埗鍙扮獥鍙ｄ繚鐣欙紙鐢ㄦ埛鍙瀵熻繍琛屾棩蹇楋紝鍏崇獥鍗抽€€鍑猴紝琛屼负涓?Docker 鐗?docker logs 涓€鑷达級銆?
a = Analysis(
    ['..\\..\\desktop\\launcher.py'],
    pathex=['..\\..\\desktop'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 绾爣鍑嗗簱鍚姩鍣ㄧ敤涓嶅埌鐨勫ぇ妯″潡锛岄€愪竴鎺掗櫎鍑忓皬浣撶Н涓庤鎶ラ潰
        'tkinter', 'unittest', 'pydoc_data', 'test',
        'setuptools', 'pip', 'distutils',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CtYun',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='ctyun_icon.ico',
)

