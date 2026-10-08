"""Validate ICO loading/compositing with Win32, using offscreen DIBs only.

No application windows are created. This does not validate taskbar behavior.
"""

import ctypes as ct
import json
import os
from ctypes import wintypes as wt
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2] / "assets" / "icons"
SIZES = (16, 24, 32, 48, 64, 128, 256)


class BitmapHeader(ct.Structure):
    _fields_ = [
        ("size", wt.DWORD),
        ("width", wt.LONG),
        ("height", wt.LONG),
        ("planes", wt.WORD),
        ("bit_count", wt.WORD),
        ("compression", wt.DWORD),
        ("image_size", wt.DWORD),
        ("x_pixels", wt.LONG),
        ("y_pixels", wt.LONG),
        ("used", wt.DWORD),
        ("important", wt.DWORD),
    ]


class BitmapInfo(ct.Structure):
    _fields_ = [("header", BitmapHeader), ("colors", wt.DWORD * 3)]


def native_render(path, size, rgb):
    user = ct.WinDLL("user32", use_last_error=True)
    gdi = ct.WinDLL("gdi32", use_last_error=True)
    user.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ct.c_int, ct.c_int, wt.UINT]
    user.LoadImageW.restype = wt.HANDLE
    user.DestroyIcon.argtypes = [wt.HANDLE]
    user.DrawIconEx.argtypes = [wt.HDC, ct.c_int, ct.c_int, wt.HANDLE, ct.c_int, ct.c_int, wt.UINT, wt.HBRUSH, wt.UINT]
    gdi.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi.CreateCompatibleDC.restype = wt.HDC
    gdi.CreateDIBSection.argtypes = [
        wt.HDC,
        ct.POINTER(BitmapInfo),
        wt.UINT,
        ct.POINTER(ct.c_void_p),
        wt.HANDLE,
        wt.DWORD,
    ]
    gdi.CreateDIBSection.restype = wt.HBITMAP
    gdi.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
    gdi.SelectObject.restype = wt.HANDLE
    gdi.DeleteObject.argtypes = [wt.HANDLE]
    gdi.DeleteDC.argtypes = [wt.HDC]
    icon = user.LoadImageW(None, str(path), 1, size, size, 0x10)
    if not icon:
        raise ct.WinError(ct.get_last_error())
    dc = gdi.CreateCompatibleDC(None)
    info = BitmapInfo(BitmapHeader(ct.sizeof(BitmapHeader), size, -size, 1, 32, 0, 0, 0, 0, 0, 0))
    address = ct.c_void_p()
    bitmap = gdi.CreateDIBSection(dc, ct.byref(info), 0, ct.byref(address), None, 0)
    if not bitmap:
        user.DestroyIcon(icon)
        gdi.DeleteDC(dc)
        raise ct.WinError(ct.get_last_error())
    previous = gdi.SelectObject(dc, bitmap)
    try:
        pixels = np.ctypeslib.as_array((ct.c_uint8 * (size * size * 4)).from_address(address.value)).reshape(
            size, size, 4
        )
        pixels[:] = [rgb[2], rgb[1], rgb[0], 255]
        if not user.DrawIconEx(dc, 0, 0, icon, size, size, 0, None, 3):
            raise ct.WinError(ct.get_last_error())
        return Image.fromarray(pixels[:, :, [2, 1, 0]].copy())
    finally:
        gdi.SelectObject(dc, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(dc)
        user.DestroyIcon(icon)


def main():
    if os.name != "nt":
        raise RuntimeError("Native Win32 preview validation requires Windows")
    validation = ROOT / "validation"
    validation.mkdir(exist_ok=True)
    records = []
    sheet = Image.new("RGB", (750, 2 * 640), "#e8e8e8")
    draw = ImageDraw.Draw(sheet)
    for row, name in enumerate(("optical_burst", "prism_spectrum")):
        xpos = 12
        draw.text((12, row * 640 + 8), name + " - native Win32 ICO compositing", fill="black")
        for size in SIZES:
            rgba = Image.open(ROOT / "png" / name / f"{size}.png").convert("RGBA")
            for j, bg in enumerate(((255, 255, 255), (48, 48, 48))):
                actual = native_render(ROOT / "windows" / f"{name}.ico", size, bg)
                expected = Image.new("RGBA", rgba.size, (*bg, 255))
                expected.alpha_composite(rgba)
                difference = np.abs(np.asarray(actual, dtype=float) - np.asarray(expected.convert("RGB"), dtype=float))
                if difference.max() > 2:
                    raise AssertionError(f"Native Windows composite diverges: {name} {size}")
                records.append(
                    {
                        "icon": name,
                        "size": size,
                        "background": list(bg),
                        "mae": float(difference.mean()),
                        "max_channel_difference": int(difference.max()),
                    }
                )
                sheet.paste(actual, (xpos, row * 640 + 40 + j * 300))
            draw.text((xpos, row * 640 + 25), str(size), fill="black")
            xpos += size + 12
    sheet.save(validation / "windows_native_preview.png")
    report = {
        "method": "LoadImageW IMAGE_ICON LR_LOADFROMFILE; DrawIconEx into offscreen 32-bit DIB",
        "taskbar_or_explorer_tested": False,
        "records": records,
    }
    (validation / "windows_native_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
