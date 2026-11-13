import Foundation

func check(_ condition:@autoclosure()->Bool,_ message:String) {
 if !condition() { fatalError(message) }
}
@main struct ModelTests {
 static func main() throws {
  if let language=ProcessInfo.processInfo.environment["FENNEC_TEST_LANGUAGE"] {
   check(AppLanguage.code==language,"Bundle selects the requested supported language")
   check(L("Серверы")==((language=="ru") ? "Серверы":"Servers"),"Native string lookup")
   check(L("Включить {0}","Мой сервер")==((language=="ru") ? "Включить Мой сервер":"Enable Мой сервер"),"Localization preserves user text")
   check(L("{0} Б/с",42)==((language=="ru") ? "42 Б/с":"42 B/s"),"Localized dynamic value")
  }
  check(L("{0} {1}","{1}","value")=="{1} value","Inserted text is not interpreted as another placeholder")
  let empty=Configuration.empty
  let data=try JSONEncoder().encode(empty)
  let decoded=try JSONDecoder().decode(Configuration.self,from:data)
  check(decoded==empty,"Configuration round-trip")
  let servers=(0..<14).map { index in Server(id:"node\(index)",name:"Example \(index)",flag:"",country_code:index==0 ? "de":"fr") }
  check(servers[0].symbol=="🇩🇪","Country code renders as a flag")
  check(chainSymbols(["node0","node1"],servers:servers)=="🇩🇪 › 🇫🇷","Chain uses ordered country flags")
  check(chainSymbols(servers.map{$0.id},servers:servers).components(separatedBy:" › ").count==14,"No five-server chain limit")
  var route=RouteGroup.empty();route.killSwitch=true
  check(route.payload["killSwitch"] as? Bool==true,"Route owns its protection switch")
  var session=EditorSession(original:"original",draft:"draft",revision:4,saving:true)
  check(!session.finish(ok:false,error:"failure"),"Failed save must keep editor open")
  check(session.draft=="draft" && session.error=="failure" && !session.saving,"Draft and error survive failed save")
  check(session.finish(ok:true,error:nil),"Successful save closes editor")
  check(session.error==nil,"Successful save clears error")
  check(moveNode("a",over:"c",in:["a","b","c"])==["b","c","a"],"Ordered chain drag")
  check(moveNode("missing",over:"b",in:["a","b"])==["a","b"],"External drag is ignored")
  check(lines(" example.invalid \n\n198.51.100.1\n")==["example.invalid","198.51.100.1"],"Rule list parsing")
  var draft=ServerDraft();draft.uri="vless://synthetic"
  check(draft.payload.count==1,"Import and manual fields never mix")
  var update=HelperUpdatePolicy()
  check(!update.begin(installed:false,needsUpdate:true,build:"one",busy:false),"First installation requires a user action")
  check(!update.begin(installed:true,needsUpdate:false,build:"one",busy:false),"Matching helper never reinstalls")
  check(!update.begin(installed:true,needsUpdate:true,build:"one",busy:true),"Updates wait for an active operation")
  check(update.begin(installed:true,needsUpdate:true,build:"one",busy:false),"Mismatch starts an automatic update")
  check(!update.begin(installed:true,needsUpdate:true,build:"one",busy:false),"Cancellation and polling cannot repeat authorization")
  var relaunched=HelperUpdatePolicy(attemptedBuild:update.attemptedBuild)
  check(!relaunched.begin(installed:true,needsUpdate:true,build:"one",busy:false),"Relaunch retains the failed-attempt latch")
  check(relaunched.begin(installed:true,needsUpdate:true,build:"two",busy:false),"A new build gets one automatic attempt")
  let runtime=HelperRuntime(resources:URL(fileURLWithPath:"/Synthetic Folder/Fennec's.app/Contents/Resources"))
  check(runtime.executable.path.hasSuffix("/Python/bin/python3"),"Use the private interpreter")
  check(Array(runtime.arguments().prefix(3))==["-I","-S","-B"],"Runtime isolation is mandatory")
  check(runtime.arguments().last=="bridge","UI uses the closed bridge entry point")
  let command=runtime.privilegedCommand(action:"install",uid:501)!
  let shell=Process();shell.executableURL=URL(fileURLWithPath:"/bin/sh")
  let output=Pipe();shell.standardOutput=output
  shell.arguments=["-c","set -- "+command+"; printf '%s\\n' \"$@\""]
  try shell.run();let result=output.fileHandleForReading.readDataToEndOfFile();shell.waitUntilExit()
  let actual=String(decoding:result,as:UTF8.self).split(separator:"\n").map(String.init)
  check(actual==[runtime.executable.path]+runtime.arguments(extra:["install","501"]),"Spaces and quotes survive administrator command encoding")
  check(runtime.privilegedCommand(action:"unexpected",uid:501)==nil,"Unknown privileged action is rejected")
  check(runtime.privilegedCommand(action:"shell",uid:501)==nil,"Only the installer can request administrator access")
  let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
  defer { try? FileManager.default.removeItem(at:folder) }
  let profile=folder.appendingPathComponent("Settings.fennec")
  try ProfileFile.write(Data("first".utf8),to:profile)
  try ProfileFile.write(Data("second".utf8),to:profile)
  let saved=try ProfileFile.read(profile)
  check(saved==Data("second".utf8),"Export replaces the selected file atomically")
  let attributes=try FileManager.default.attributesOfItem(atPath:profile.path)
  check((attributes[.posixPermissions] as? NSNumber)?.intValue==0o600,"Export is private from creation")
  print("Swift model checks passed")
 }
}
