import Foundation
import Darwin
import CryptoKit

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
  var routed=Configuration.empty
  check(appStatus(installed:false,needsUpdate:false,state:"off",configuration:routed)==AppStatus.needsHelper,"Missing helper comes first")
  check(appStatus(installed:true,needsUpdate:true,state:"on",configuration:routed)==AppStatus.updating,"Pending helper update hides routing state")
  check(appStatus(installed:true,needsUpdate:false,state:"off",configuration:routed)==AppStatus.needsServer,"No servers asks for setup")
  routed.nodes=[servers[0]];routed.fallback.nodes=["node0"]
  check(appStatus(installed:true,needsUpdate:false,state:"off",configuration:routed)==AppStatus.off,"Disabled routes are direct")
  routed.fallback.killSwitch=true
  check(appStatus(installed:true,needsUpdate:false,state:"on",configuration:routed)==AppStatus.blocked,"Disabled protected route blocks traffic")
  routed.fallback.enabled=true
  check(appStatus(installed:true,needsUpdate:false,state:"starting",configuration:routed)==AppStatus.starting,"Green status waits for the tunnel")
  check(appStatus(installed:true,needsUpdate:false,state:"on",configuration:routed)==AppStatus.active(routes:1,total:1),"Active routes are counted")
  check(appStatus(installed:true,needsUpdate:false,state:"error",configuration:routed)==AppStatus.failure,"Engine failure wins over routes")
  routed.bypass=true
  check(appStatus(installed:true,needsUpdate:false,state:"bypass",configuration:routed)==AppStatus.bypass,"Bypass is visible")
  let synthetic=String(repeating:"A",count:43)+"="
  var remoteDraft=RemoteDraft(RemoteAccess(enabled:false,route:"fallback",localPort:3773,address:"vps.example.invalid",port:51820,serverPublicKey:synthetic,tunnelAddress:"10.77.0.2",peerAddress:"10.77.0.1",publicKey:synthetic))
  check(remoteDraft.localPort=="3773" && remoteDraft.route=="fallback" && !remoteDraft.enabled,"Remote draft mirrors saved settings")
  remoteDraft.address=" vps.example.invalid "
  let remotePayload=remoteDraft.payload()
  check(remotePayload["address"] as? String=="vps.example.invalid" && remotePayload["port"] as? Int==51820 && remotePayload["rotateKeys"] as? Bool==false,"Remote payload is trimmed and typed")
  check(remotePayload["privateKey"]==nil && remotePayload["publicKey"]==nil,"The interface never sends keys")
  check(remoteDraft.payload(rotateKeys:true)["rotateKeys"] as? Bool==true,"Rotation is explicit")
  check(RemoteDraft().localPort.isEmpty && RemoteDraft().route.isEmpty,"No default port or route")
  check(wireGuardPeer(publicKey:"KEY",tunnelAddress:"10.77.0.2")=="[Peer]\nPublicKey = KEY\nAllowedIPs = 10.77.0.2/32\n","Peer block for IPv4")
  check(wireGuardPeer(publicKey:"KEY",tunnelAddress:"fd00::2").contains("fd00::2/128"),"Peer block for IPv6")
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
  check(mergedLines("a\nb",["b","c"])=="a\nb\nc","Expanded rule sets merge without duplicates")
  var withSets=RouteGroup.empty();withSets.keywords=["kw"];withSets.regexes=["^a$"]
  withSets.ruleSets=[RuleSet(kind:"custom",name:"Mine",digest:"a",url:"https://example.invalid/a.srs"),RuleSet(kind:"geoip",name:"de",digest:"b"),RuleSet(kind:"custom",name:"Local",digest:"c")]
  let setsPayload=withSets.payload["ruleSets"] as? [[String:Any]]
  check(setsPayload?.count==3 && setsPayload?[0]["url"] as? String=="https://example.invalid/a.srs" && setsPayload?[1]["url"]==nil,"Rule sets keep their source in the payload")
  check(withSets.payload["keywords"] as? [String]==["kw"] && withSets.payload["regexes"] as? [String]==["^a$"],"Text matchers reach the payload")
  check(withSets.payload["snapshots"]==nil,"The legacy field is no longer sent")
  check(withSets.ruleSets!.map(\.updatable)==[true,true,false],"Only sets with a source can update")
  var fallbackRoute=RouteGroup.empty();fallbackRoute.id="fallback"
  check(fallbackRoute.payload["ruleSets"]==nil,"Fallback has no matchers")
  let migrated=try JSONDecoder().decode(RouteGroup.self,from:Data(#"{"id":"a","title":"t","flag":"","enabled":false,"nodes":[],"ruleSets":[{"kind":"custom","name":"aaaaaaaaaaaa","digest":"aa"}]}"#.utf8))
  check(migrated.ruleSets?.first?.name=="aaaaaaaaaaaa" && migrated.ruleSets?.first?.url==nil,"Helper rule sets decode")
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
  let command=runtime.privilegedCommand(action:"install",uid:501,digest:String(repeating:"a",count:64))!
  let shell=Process();shell.executableURL=URL(fileURLWithPath:"/bin/sh")
  let output=Pipe();shell.standardOutput=output
  shell.arguments=["-c","set -- "+command+"; printf '%s\\n' \"$@\""]
  try shell.run();let result=output.fileHandleForReading.readDataToEndOfFile();shell.waitUntilExit()
  let actual=String(decoding:result,as:UTF8.self).split(separator:"\n").map(String.init)
  check(actual.prefix(5)==["/usr/bin/env","-i","PATH=/usr/bin:/bin:/usr/sbin:/sbin","/bin/sh","-c"],"Use only system tools before verifying the installation payload")
  check(actual.dropFirst(5).joined(separator:"\n").contains("Fennec'\\''s.app"),"Spaces and quotes survive administrator command encoding")
  check(command.contains("shasum") && command.contains("HelperInstall.zip"),"Installation verifies the staged payload before executing it")
  let escaped=command.replacingOccurrences(of:"\\",with:"\\\\").replacingOccurrences(of:"\"",with:"\\\"")
  var scriptError:NSDictionary?
  let authorization=NSAppleScript(source:"do shell script \"\(escaped)\" with administrator privileges")
  check(authorization?.compileAndReturnError(&scriptError)==true,"Administrator script parses without requesting authorization")
  check(runtime.privilegedCommand(action:"install",uid:501)==nil,"Model builds cannot install unpinned code")
  check(runtime.privilegedCommand(action:"install",uid:501,digest:"untrusted")==nil,"Invalid payload identities are rejected")
  check(runtime.privilegedCommand(action:"unexpected",uid:501)==nil,"Unknown privileged action is rejected")
  check(runtime.privilegedCommand(action:"shell",uid:501)==nil,"Only the installer can request administrator access")
  let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
  defer { try? FileManager.default.removeItem(at:folder) }
  let profile=folder.appendingPathComponent("Settings.fennec")
  let installerResources=folder.appendingPathComponent("Fennec's Resources")
  let payload=folder.appendingPathComponent("payload")
  let fakePython=payload.appendingPathComponent("Python/bin/python3")
  try FileManager.default.createDirectory(at:fakePython.deletingLastPathComponent(),withIntermediateDirectories:true)
  try FileManager.default.createDirectory(at:installerResources,withIntermediateDirectories:true)
  try Data("#!/bin/sh\nprintf '{\"ok\":true}\\n'\n".utf8).write(to:fakePython)
  try FileManager.default.setAttributes([.posixPermissions:0o755],ofItemAtPath:fakePython.path)
  let archive=installerResources.appendingPathComponent("HelperInstall.zip")
  let pack=Process();pack.executableURL=URL(fileURLWithPath:"/usr/bin/ditto")
  pack.arguments=["-c","-k","--norsrc",payload.path,archive.path]
  try pack.run();pack.waitUntilExit();check(pack.terminationStatus==0,"Pack installerResources installation")
  let digest=SHA256.hash(data:try Data(contentsOf:archive)).map { String(format:"%02x",$0) }.joined()
  let installer=HelperRuntime(resources:installerResources)
  func runInstaller(_ digest:String)throws->(Int32,Data) {
   let shell=Process();shell.executableURL=URL(fileURLWithPath:"/bin/sh")
   shell.arguments=["-c",installer.privilegedCommand(action:"install",uid:501,digest:digest)!]
   let output=Pipe();shell.standardOutput=output;shell.standardError=FileHandle.nullDevice
   try shell.run();let result=output.fileHandleForReading.readDataToEndOfFile();shell.waitUntilExit()
   return (shell.terminationStatus,result)
  }
  let accepted=try runInstaller(digest)
  check(accepted.0==0 && String(decoding:accepted.1,as:UTF8.self).contains("\"ok\":true"),"Pinned installerResources payload executes from a private stage")
  try Data("tampered".utf8).write(to:archive)
  let rejected=try runInstaller(digest)
  check(rejected.0 != 0 && rejected.1.isEmpty,"Changed payload never executes candidate code")
  try ProfileFile.write(Data("first".utf8),to:profile)
  try ProfileFile.write(Data("second".utf8),to:profile)
  let saved=try ProfileFile.read(profile)
  check(saved==Data("second".utf8),"Export replaces the selected file atomically")
  let attributes=try FileManager.default.attributesOfItem(atPath:profile.path)
  check((attributes[.posixPermissions] as? NSNumber)?.intValue==0o600,"Export is private from creation")
  func rejectsProfile(_ url:URL) {
   do { _=try ProfileFile.read(url);fatalError("Unsafe profile was accepted") } catch {}
  }
  let fifo=folder.appendingPathComponent("pipe.fennec")
  check(mkfifo(fifo.path,mode_t(0o600))==0,"Create a synthetic FIFO")
  let started=ProcessInfo.processInfo.systemUptime
  rejectsProfile(fifo)
  check(ProcessInfo.processInfo.systemUptime-started<1,"A FIFO cannot block profile import")
  let link=folder.appendingPathComponent("link.fennec")
  try FileManager.default.createSymbolicLink(at:link,withDestinationURL:profile)
  rejectsProfile(link);rejectsProfile(folder)
  let oversized=folder.appendingPathComponent("large.fennec")
  let fd=Darwin.open(oversized.path,O_WRONLY|O_CREAT|O_EXCL,mode_t(0o600))
  check(fd>=0,"Create an oversized synthetic file")
  check(ftruncate(fd,off_t(ProfileFile.maxBytes+1))==0,"Create sparse oversized profile")
  Darwin.close(fd);rejectsProfile(oversized)
  print("Swift model checks passed")
 }
}
