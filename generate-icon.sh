#!/bin/bash
# Generate the Quarterdeck app icon with correct pixel dimensions
set -e
cd "$(dirname "$0")"

ICONSET="icon.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"

source venv/bin/activate
python3 -c "
from AppKit import (
    NSColor, NSFont, NSString, NSFontAttributeName,
    NSForegroundColorAttributeName, NSBezierPath,
    NSGraphicsContext, NSBitmapImageRep, NSPNGFileType,
    NSCalibratedRGBColorSpace, NSMakeRect
)
from Foundation import NSDictionary, NSMakePoint, NSMakeSize
import objc

def make_icon(pixel_size, output_path):
    # Create bitmap at exact pixel dimensions (72 DPI, 1x scale)
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, pixel_size, pixel_size, 8, 4, True, False,
        NSCalibratedRGBColorSpace, 0, 0
    )
    rep.setSize_(NSMakeSize(pixel_size, pixel_size))  # 72 DPI

    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.setCurrentContext_(ctx)

    size = pixel_size
    # macOS icon: ~10% padding, rounded rect (squircle) with ~22% corner radius
    padding = int(size * 0.1)
    content_size = size - (padding * 2)
    cx = padding
    cy = padding

    # Dark rounded rect (squircle shape baked in, transparent background)
    bg = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.12, 0.12, 0.14, 1.0)
    bg.set()
    corner_radius = content_size * 0.22
    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx, cy, content_size, content_size),
        corner_radius, corner_radius
    )
    path.fill()

    # Green dot
    green = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.2, 0.9, 0.4, 1.0)
    green.set()
    dot_size = content_size * 0.13
    dot_path = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx + content_size * 0.74, cy + content_size * 0.74, dot_size, dot_size)
    )
    dot_path.fill()

    # Quarterdeck monogram
    if size >= 32:
        font_size = content_size * 0.5
        font = NSFont.fontWithName_size_('SF Pro Display Bold', font_size)
        if not font:
            font = NSFont.boldSystemFontOfSize_(font_size)
        attrs = NSDictionary.dictionaryWithObjectsAndKeys_(
            font, NSFontAttributeName,
            NSColor.whiteColor(), NSForegroundColorAttributeName,
            None
        )
        text = NSString.stringWithString_('Q')
        text_size = text.sizeWithAttributes_(attrs)
        x = cx + (content_size - text_size.width) / 2
        y = cy + (content_size - text_size.height) / 2
        text.drawAtPoint_withAttributes_(NSMakePoint(x, y), attrs)

    NSGraphicsContext.setCurrentContext_(None)

    png_data = rep.representationUsingType_properties_(NSPNGFileType, None)
    png_data.writeToFile_atomically_(output_path, True)

iconset = '$ICONSET'

entries = [
    (16,   'icon_16x16.png'),
    (32,   'icon_16x16@2x.png'),
    (32,   'icon_32x32.png'),
    (64,   'icon_32x32@2x.png'),
    (128,  'icon_128x128.png'),
    (256,  'icon_128x128@2x.png'),
    (256,  'icon_256x256.png'),
    (512,  'icon_256x256@2x.png'),
    (512,  'icon_512x512.png'),
    (1024, 'icon_512x512@2x.png'),
]

for pixel_size, filename in entries:
    make_icon(pixel_size, f'{iconset}/{filename}')
    print(f'  {filename} ({pixel_size}px)')
"

iconutil -c icns "$ICONSET" -o icon.icns
rm -rf "$ICONSET"
echo "✅ icon.icns generated"
