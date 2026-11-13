import Cocoa

enum MenuBarStatus:Equatable {
 case normal,dimmed,attention,failure
}
func menuBarStatus(_ status:AppStatus)->MenuBarStatus {
 switch status {
 case .active:return .normal
 case .failure,.blocked:return .failure
 case .bypass:return .attention
 default:return .dimmed
 }
}
func menuBarImage(size:CGFloat,status:MenuBarStatus = .normal)->NSImage {
 let image=NSImage(size:NSSize(width:size,height:size),flipped:true) { _ in
  NSGraphicsContext.saveGraphicsState()
  defer { NSGraphicsContext.restoreGraphicsState() }
  let transform=NSAffineTransform();transform.scale(by:size/24);transform.concat()
  let ink=status == .normal || status == .dimmed ? NSColor.black.withAlphaComponent(status == .dimmed ? 0.45:1):NSColor.labelColor
  ink.setStroke()
  let outline=NSBezierPath()
  outline.move(to:NSPoint(x:3,y:2.5))
  outline.curve(to:NSPoint(x:9,y:8),controlPoint1:NSPoint(x:5.5,y:3),controlPoint2:NSPoint(x:7.5,y:5.5))
  outline.curve(to:NSPoint(x:15,y:8),controlPoint1:NSPoint(x:11,y:7.3),controlPoint2:NSPoint(x:13,y:7.3))
  outline.curve(to:NSPoint(x:21,y:2.5),controlPoint1:NSPoint(x:16.5,y:5.5),controlPoint2:NSPoint(x:18.5,y:3))
  outline.curve(to:NSPoint(x:20,y:14),controlPoint1:NSPoint(x:21.5,y:7),controlPoint2:NSPoint(x:21.5,y:11))
  outline.curve(to:NSPoint(x:12,y:21),controlPoint1:NSPoint(x:18,y:17),controlPoint2:NSPoint(x:15,y:19.5))
  outline.curve(to:NSPoint(x:4,y:14),controlPoint1:NSPoint(x:9,y:19.5),controlPoint2:NSPoint(x:6,y:17))
  outline.curve(to:NSPoint(x:3,y:2.5),controlPoint1:NSPoint(x:2.5,y:11),controlPoint2:NSPoint(x:2.5,y:7))
  outline.close();outline.lineWidth=1.65;outline.lineJoinStyle = .round;outline.stroke()
  let details=NSBezierPath();details.lineWidth=1.5;details.lineCapStyle = .round
  for points:[NSPoint] in [[NSPoint(x:5.5,y:6),NSPoint(x:6,y:9)], [NSPoint(x:18.5,y:6),NSPoint(x:18,y:9)], [NSPoint(x:7,y:12.5),NSPoint(x:9,y:13.5)], [NSPoint(x:17,y:12.5),NSPoint(x:15,y:13.5)]] {
   details.move(to:points[0]);details.line(to:points[1])
  }
  details.stroke()
  ink.setFill()
  let nose=NSBezierPath();nose.move(to:NSPoint(x:10.5,y:16.5));nose.line(to:NSPoint(x:13.5,y:16.5));nose.line(to:NSPoint(x:12,y:18.2));nose.close();nose.fill()
  if status == .attention || status == .failure {
   (status == .failure ? NSColor.systemRed:NSColor.systemOrange).setFill()
   NSBezierPath(ovalIn:NSRect(x:15.5,y:0.5,width:8,height:8)).fill()
  }
  return true
 }
 image.isTemplate=status == .normal || status == .dimmed;return image
}

enum BrandAssets {
 static let darkWordmark=load("libvpn-wordmark-dark")
 static let lightWordmark=load("libvpn-wordmark-light")
 private static func load(_ name:String)->NSImage {
  NSImage(contentsOf:Bundle.main.url(forResource:name,withExtension:"pdf")!)!
 }
 static func wordmark(dark:Bool)->NSImage { dark ? darkWordmark:lightWordmark }
}
