#!/usr/bin/env python3
"""
scripts/build_fonts.py

Reproducible font download, subsetting, and WOFF2 conversion script for:
- Noto Serif Devanagari (variable font)
- Noto Serif (variable font)

Sources: Official Google Fonts upstream repository (SIL Open Font License).
Tooling: FontTools + Brotli.
Target: app/static/fonts/
"""

import os
import io
import sys
import urllib.request
from fontTools import subset, ttLib

# Ensure stdout handles UTF-8 safely on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "fonts")

URL_NOTO_SERIF_DEV = "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifdevanagari/NotoSerifDevanagari%5Bwdth%2Cwght%5D.ttf"
URL_OFL_DEV = "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifdevanagari/OFL.txt"

URL_NOTO_SERIF_LATIN = "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserif/NotoSerif%5Bwdth%2Cwght%5D.ttf"
URL_OFL_LATIN = "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserif/OFL.txt"

# Target filenames
DEV_OUTPUT_FILENAME = "noto-serif-devanagari-variable.woff2"
LATIN_OUTPUT_FILENAME = "noto-serif-variable.woff2"
DEV_OFL_FILENAME = "OFL-NotoSerifDevanagari.txt"
LATIN_OFL_FILENAME = "OFL-NotoSerif.txt"


def download_bytes(url: str) -> bytes:
    print(f"Downloading: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Failed to download {url}: HTTP {resp.status}")
        data = resp.read()
        print(f"Downloaded {len(data):,} bytes")
        return data


def build_devanagari_subset(raw_bytes: bytes) -> bytes:
    print("\n--- Subsetting Noto Serif Devanagari ---")
    font = ttLib.TTFont(io.BytesIO(raw_bytes))

    options = subset.Options()
    options.flavor = "woff2"
    # Unicode ranges: U+0900-U+097F, U+200C-U+200D (ZWNJ/ZWJ), U+25CC (Dotted circle), U+0020 (Space)
    unicodes = list(range(0x0900, 0x0980)) + [0x200C, 0x200D, 0x25CC, 0x0020]

    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=unicodes)
    subsetter.subset(font)

    font.flavor = "woff2"
    out = io.BytesIO()
    font.save(out)
    woff2_bytes = out.getvalue()
    print(f"Generated {DEV_OUTPUT_FILENAME}: {len(woff2_bytes):,} bytes")
    return woff2_bytes


def build_latin_subset(raw_bytes: bytes) -> bytes:
    print("\n--- Subsetting Noto Serif (Latin) ---")
    font = ttLib.TTFont(io.BytesIO(raw_bytes))

    options = subset.Options()
    options.flavor = "woff2"
    # Unicode ranges:
    # U+0020–U+007E (Basic Latin)
    # U+00A0 (NBSP)
    # U+2013–U+2014 (En-dash, em-dash)
    # U+2018–U+201D (Curly quotes)
    # U+2022 (Bullet)
    # U+2026 (Ellipsis)
    # U+20B9 (Indian Rupee symbol)
    unicodes = (
        list(range(0x0020, 0x007F))
        + [0x00A0, 0x2013, 0x2014]
        + list(range(0x2018, 0x201E))
        + [0x2022, 0x2026, 0x20B9]
    )

    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=unicodes)
    subsetter.subset(font)

    font.flavor = "woff2"
    out = io.BytesIO()
    font.save(out)
    woff2_bytes = out.getvalue()
    print(f"Generated {LATIN_OUTPUT_FILENAME}: {len(woff2_bytes):,} bytes")
    return woff2_bytes


def validate_font(file_path: str, is_devanagari: bool):
    print(f"\nValidating {os.path.basename(file_path)}:")
    with open(file_path, "rb") as fp:
        data = fp.read()

    # Magic byte check
    assert data[:4] == b"wOF2", f"Invalid WOFF2 signature: {data[:4]}"
    print("  [OK] Valid WOFF2 signature (wOF2)")

    font = ttLib.TTFont(io.BytesIO(data))
    assert font.flavor == "woff2", f"Unexpected flavor: {font.flavor}"

    # Variable font axis check (400 through 700)
    assert "fvar" in font, "Missing 'fvar' table: font is not variable"
    weight_axis = None
    for axis in font["fvar"].axes:
        if axis.axisTag == "wght":
            weight_axis = axis
            break
    assert weight_axis is not None, "Missing 'wght' axis in fvar table"
    assert weight_axis.minValue <= 400 and weight_axis.maxValue >= 700, (
        f"Weight range [{weight_axis.minValue}, {weight_axis.maxValue}] does not cover 400..700"
    )
    print(f"  [OK] Weight axis: [{weight_axis.minValue} .. {weight_axis.maxValue}] (covers 400 through 700)")

    # Glyph cmap checks
    cmap = font.getBestCmap()
    if is_devanagari:
        # Check Devanagari consonants, vowels, matras, ZWNJ, ZWJ, dotted circle
        test_cps = [0x0915, 0x0930, 0x093E, 0x094D, 0x200C, 0x200D, 0x25CC]
        for cp in test_cps:
            assert cp in cmap, f"Devanagari font missing codepoint U+{cp:04X}"
        assert "GSUB" in font, "Missing GSUB table for Devanagari shaping"
        print(f"  [OK] Devanagari cmap contains required characters (total codepoints in cmap: {len(cmap)})")
        print("  [OK] GSUB shaping table present")
    else:
        # Check basic Latin, punctuation, rupee symbol
        test_cps = [0x0041, 0x0061, 0x0030, 0x2014, 0x2026, 0x20B9]
        for cp in test_cps:
            assert cp in cmap, f"Latin font missing codepoint U+{cp:04X}"
        print(f"  [OK] Latin cmap contains required characters (total codepoints in cmap: {len(cmap)})")
        print("  [OK] Indian Rupee symbol (U+20B9) present")


def main():
    os.makedirs(FONTS_DIR, exist_ok=True)

    # 1. Download source fonts and licenses
    dev_raw = download_bytes(URL_NOTO_SERIF_DEV)
    dev_ofl = download_bytes(URL_OFL_DEV)

    latin_raw = download_bytes(URL_NOTO_SERIF_LATIN)
    latin_ofl = download_bytes(URL_OFL_LATIN)

    # 2. Subset fonts
    dev_woff2 = build_devanagari_subset(dev_raw)
    latin_woff2 = build_latin_subset(latin_raw)

    # 3. Save files
    dev_path = os.path.join(FONTS_DIR, DEV_OUTPUT_FILENAME)
    with open(dev_path, "wb") as fp:
        fp.write(dev_woff2)

    latin_path = os.path.join(FONTS_DIR, LATIN_OUTPUT_FILENAME)
    with open(latin_path, "wb") as fp:
        fp.write(latin_woff2)

    with open(os.path.join(FONTS_DIR, DEV_OFL_FILENAME), "wb") as fp:
        fp.write(dev_ofl)

    with open(os.path.join(FONTS_DIR, LATIN_OFL_FILENAME), "wb") as fp:
        fp.write(latin_ofl)

    print(f"\nSaved font files and licenses to: {FONTS_DIR}")

    # 4. Validate
    validate_font(dev_path, is_devanagari=True)
    validate_font(latin_path, is_devanagari=False)

    print("\n[SUCCESS] All fonts successfully downloaded, subsetted, converted, and verified.")


if __name__ == "__main__":
    main()
