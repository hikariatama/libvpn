import AppKit

func bitmap(width:Int,height:Int,scale:Int,draw:()->Void)->NSBitmapImageRep {
 let rep=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:width*scale,pixelsHigh:height*scale,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
 NSGraphicsContext.saveGraphicsState()
 let context=NSGraphicsContext(bitmapImageRep:rep)!
 NSGraphicsContext.current=context;context.cgContext.scaleBy(x:CGFloat(scale),y:CGFloat(scale))
 draw();context.flushGraphics();NSGraphicsContext.restoreGraphicsState()
 rep.size=NSSize(width:width,height:height)
 return rep
}
func applicationIcon(_ pixels:Int)->NSBitmapImageRep {
 let source=NSImage(contentsOfFile:"Resources/Branding/libvpn-icon.png")!
 return bitmap(width:pixels,height:pixels,scale:1) {
  NSGraphicsContext.current?.imageInterpolation = .high
  source.draw(in:NSRect(x:0,y:0,width:pixels,height:pixels))
 }
}
final class WordmarkView:NSView {
 let source:NSImage
 init(source:NSImage) { self.source=source;super.init(frame:NSRect(x:0,y:0,width:161,height:59)) }
 required init?(coder:NSCoder) { fatalError("init(coder:) is unsupported") }
 override func draw(_ dirtyRect:NSRect) { source.draw(in:bounds) }
}
let bannerSize=NSSize(width:654,height:422)
func bannerSource(_ path:String) throws -> NSBitmapImageRep {
 guard let source=NSBitmapImageRep(data:try Data(contentsOf:URL(fileURLWithPath:path))),
       source.pixelsWide==1308,source.pixelsHigh==844 else {
  throw NSError(domain:"Artwork",code:1,userInfo:[NSLocalizedDescriptionKey:"DMG banner must be 1308×844 pixels (2×)."])
 }
 source.size=bannerSize
 return source
}
func background() throws -> [NSBitmapImageRep] {
 let source=try bannerSource("Tools/Artwork/libvpn-dmg-banner.png")
 let image=NSImage(size:bannerSize);image.addRepresentation(source)
 let standard=bitmap(width:654,height:422,scale:1) {
  NSGraphicsContext.current?.imageInterpolation = .high
  image.draw(in:NSRect(origin:.zero,size:bannerSize))
 }
 // Keep the original 2× pixels. Only the 1× representation is resampled.
 return [standard,source]
}
func verifyBackground(_ path:String,sourcePath:String) throws {
 let source=try bannerSource(sourcePath)
 let reps=NSBitmapImageRep.imageReps(with:try Data(contentsOf:URL(fileURLWithPath:path)))
 guard reps.count==2 else { throw NSError(domain:"Artwork",code:2,userInfo:[NSLocalizedDescriptionKey:"DMG background must include 1× and 2× representations."]) }
 for scale in [1,2] {
  guard let rep=reps.compactMap({$0 as? NSBitmapImageRep}).first(where:{$0.pixelsWide==654*scale && $0.pixelsHigh==422*scale}),
        abs(rep.size.width-bannerSize.width)<0.01,abs(rep.size.height-bannerSize.height)<0.01 else {
   throw NSError(domain:"Artwork",code:3,userInfo:[NSLocalizedDescriptionKey:"Incorrect DMG background dimensions or DPI."])
  }
  if scale==2 {
   guard rep.bitsPerSample==source.bitsPerSample,rep.samplesPerPixel==source.samplesPerPixel,
         rep.bitmapFormat==source.bitmapFormat,let actual=rep.bitmapData,let original=source.bitmapData else {
    throw NSError(domain:"Artwork",code:4,userInfo:[NSLocalizedDescriptionKey:"DMG banner pixel format changed."])
   }
   let rowBytes=source.pixelsWide*source.bitsPerPixel/8
   for row in 0..<source.pixelsHigh {
    guard memcmp(actual+row*rep.bytesPerRow,original+row*source.bytesPerRow,rowBytes)==0 else {
     throw NSError(domain:"Artwork",code:5,userInfo:[NSLocalizedDescriptionKey:"DMG banner pixels changed."])
    }
   }
  }
 }
}
@main struct Artwork {
 static func main() throws {
  if CommandLine.arguments.count==4,CommandLine.arguments[1]=="--verify-background" {
   try verifyBackground(CommandLine.arguments[2],sourcePath:CommandLine.arguments[3]);return
  }
  guard CommandLine.arguments.count==4 else { fatalError("Usage: artwork OUTPUT VERSION ARCH") }
  let output=URL(fileURLWithPath:CommandLine.arguments[1],isDirectory:true)
  let icons=output.appendingPathComponent("libvpn.iconset",isDirectory:true)
  try FileManager.default.createDirectory(at:icons,withIntermediateDirectories:true)
  for size in [16,32,128,256,512] {
   for scale in [1,2] {
    let suffix=scale==1 ? "":"@2x"
    let rep=applicationIcon(size*scale)
    try rep.representation(using:.png,properties:[:])!.write(to:icons.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
   }
  }
  let svg=try String(contentsOfFile:"Resources/Branding/libvpn.svg",encoding:.utf8)
  for mode in ["dark","light"] {
   let content=mode=="dark" ? svg:svg.replacingOccurrences(of:"fill=\"white\"",with:"fill=\"black\"")
   let wordmark=WordmarkView(source:NSImage(data:Data(content.utf8))!)
   try wordmark.dataWithPDF(inside:wordmark.bounds).write(to:output.appendingPathComponent("libvpn-wordmark-\(mode).pdf"))
  }
  let reps=try background()
  try NSBitmapImageRep.tiffRepresentationOfImageReps(in:reps,using:.lzw,factor:1)!.write(to:output.appendingPathComponent("background.tiff"))
  try reps[1].representation(using:.png,properties:[:])!.write(to:output.appendingPathComponent("background-preview.png"))
  try applicationIcon(1024).representation(using:.png,properties:[:])!.write(to:output.appendingPathComponent("icon-preview.png"))
 }
}
