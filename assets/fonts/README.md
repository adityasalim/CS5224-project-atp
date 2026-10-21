# Fonts for the PDF (layer `tp-fonts`)

`tp-render-pdf` uses these so place names in any language print correctly instead of `????`.

| File | Covers | Source / licence |
| --- | --- | --- |
| `DejaVuSans*.ttf` | Latin (incl. accents, Vietnamese), Greek, Cyrillic, Arabic, Hebrew | DejaVu Fonts, free licence (`LICENSE-DejaVu.txt`) |
| `TPCJK-Regular.ttf`, `TPCJK-Bold.ttf` | Japanese, Chinese (simplified and traditional), Korean | Noto Sans CJK JP converted to TrueType outlines (fpdf2 needs TrueType) by `scripts/make_cjk_font.py`, SIL Open Font License 1.1 (`LICENSE-NotoSansCJK.txt`) |

Not covered: Thai, Devanagari and other Indic scripts (add a Noto Sans font for them and register it as another fallback).

Build and publish the layer from CloudShell (repo folder): `bash scripts/build_fonts_layer.sh`.
The layer unzips to `/opt/fonts`, which is where `tp-render-pdf` looks (`FONT_DIR`).
