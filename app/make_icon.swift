// Draws Mike's app icon (1024×1024 PNG): a warm charcoal tile with Mike's three clay voice bars.
// Usage: swift make_icon.swift out.png
import AppKit

let size: CGFloat = 1024
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                           bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let ctx = NSGraphicsContext.current!.cgContext
let rgb = CGColorSpaceCreateDeviceRGB()
func c(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> CGColor { CGColor(red: r / 255, green: g / 255, blue: b / 255, alpha: a) }

// macOS icon grid: 824pt tile centered in 1024, ~186pt corner radius.
let tile = CGRect(x: 100, y: 100, width: 824, height: 824)
let path = CGPath(roundedRect: tile, cornerWidth: 186, cornerHeight: 186, transform: nil)

// drop shadow
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -12), blur: 32, color: c(0, 0, 0, 0.35))
ctx.addPath(path); ctx.setFillColor(c(38, 38, 36)); ctx.fillPath()
ctx.restoreGState()

// charcoal, very slightly lighter at the top
ctx.saveGState()
ctx.addPath(path); ctx.clip()
let fill = CGGradient(colorsSpace: rgb, colors: [c(48, 48, 46), c(31, 30, 29)] as CFArray, locations: [0, 1])!
ctx.drawLinearGradient(fill, start: CGPoint(x: 0, y: 924), end: CGPoint(x: 0, y: 100), options: [])
ctx.restoreGState()

// hairline edge
ctx.addPath(CGPath(roundedRect: tile.insetBy(dx: 2, dy: 2), cornerWidth: 184, cornerHeight: 184, transform: nil))
ctx.setStrokeColor(c(222, 220, 209, 0.10)); ctx.setLineWidth(4); ctx.strokePath()

// three clay bars, same proportions as the in-app mark
let barW: CGFloat = 72, gap: CGFloat = 60
let heights: [CGFloat] = [190, 410, 285]
let totalW = barW * 3 + gap * 2
var x = 512 - totalW / 2
ctx.setFillColor(c(217, 119, 87))
for h in heights {
    let r = CGRect(x: x, y: 512 - h / 2, width: barW, height: h)
    ctx.addPath(CGPath(roundedRect: r, cornerWidth: barW / 2, cornerHeight: barW / 2, transform: nil))
    ctx.fillPath()
    x += barW + gap
}

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
