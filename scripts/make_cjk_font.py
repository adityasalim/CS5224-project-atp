"""Convert one face of the Noto Sans CJK collection (CFF outlines) into a TrueType font that fpdf2 can embed."""
import sys, time
from fontTools.ttLib import TTCollection, TTFont, newTable
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools import subset

# Usage: python3 scripts/make_cjk_font.py NotoSansCJK-Regular.ttc 0 assets/fonts/TPCJK-Regular.ttf "TP CJK"
# (face 0 of the Noto Sans CJK collection is JP; needs: pip install fonttools)
src, idx, out, family = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
t=time.time()
font = TTCollection(src).fonts[idx]
# keep every mapped character, drop layout features we don't need (smaller, simpler)
opts = subset.Options(); opts.layout_features = []; opts.name_IDs = ['*']; opts.notdef_outline = True
opts.glyph_names = False; opts.hinting = False; opts.drop_tables += ['GSUB','GPOS','GDEF','BASE','VORG','vhea','vmtx','DSIG']
sub = subset.Subsetter(opts); sub.populate(unicodes=font.getBestCmap().keys()); sub.subset(font)
glyphOrder = font.getGlyphOrder(); gs = font.getGlyphSet()
glyf = newTable('glyf'); glyf.glyphOrder = glyphOrder; glyf.glyphs = {}
for name in glyphOrder:
    pen = TTGlyphPen(gs); gs[name].draw(Cu2QuPen(pen, 1.0, reverse_direction=True)); glyf.glyphs[name] = pen.glyph()
font['glyf'] = glyf; font['loca'] = newTable('loca')
del font['CFF ']
font['maxp'] = maxp = newTable('maxp'); maxp.tableVersion = 0x00010000
for a in ('maxZones','maxTwilightPoints','maxStorage','maxFunctionDefs','maxInstructionDefs','maxStackElements','maxSizeOfInstructions','maxComponentElements'): setattr(maxp, a, 0)
maxp.maxZones = 1; maxp.numGlyphs = len(glyphOrder)
font['post'].formatType = 3.0
font['head'].glyphDataFormat = 0
font.sfntVersion = '\x00\x01\x00\x00'
# rename family so it's clear this is a converted copy (OFL reserved-name courtesy)
for rec in font['name'].names:
    if rec.nameID in (1, 4, 16): rec.string = family + ('' if rec.nameID != 4 else '')
font.save(out)
print(out, len(glyphOrder), 'glyphs', round(time.time()-t,1), 's')
