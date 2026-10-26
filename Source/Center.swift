import Cocoa
import SwiftUI
import ServiceManagement

func bridgeRequest(_ request:[String:Any],completion:@escaping(Reply)->Void) {
 DispatchQueue.global(qos:.utility).async {
  let runtime=HelperRuntime(resources:Bundle.main.resourceURL!)
  let process=Process();process.executableURL=runtime.executable
  process.arguments=runtime.arguments()
  process.environment=ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("PYTHON") && !$0.key.hasPrefix("DYLD_") }
  let input=Pipe(),output=Pipe();process.standardInput=input;process.standardOutput=output;process.standardError=FileHandle.nullDevice
  var reply=Reply(ok:false,error:L("Системный помощник недоступен"))
  do {
   var localized=request;localized["language"]=AppLanguage.code
   var data=try JSONSerialization.data(withJSONObject:localized);data.append(10)
   try process.run();try input.fileHandleForWriting.write(contentsOf:data);try input.fileHandleForWriting.close()
   let result=output.fileHandleForReading.readDataToEndOfFile();process.waitUntilExit()
   reply=(try? JSONDecoder().decode(Reply.self,from:result)) ?? reply
  } catch {}
  DispatchQueue.main.async { completion(reply) }
 }
}
final class Center:ObservableObject {
 @Published var configuration=Configuration.empty
 @Published var installed=false
 @Published var needsUpdate=false
 @Published var recoveryRequired=false
 @Published var authorizingHelper=false
 @Published var state="off"
 @Published var error:String?
 @Published var busy:String?
 @Published var editingChain:RouteGroup?
 @Published var paths:[Reachability]=[]
 @Published var history:[Double]=[]
 @Published var rxRate=0.0
 @Published var txRate=0.0
 @Published var profileImport:ProfileImport?
 @Published var launchAtLogin=SMAppService.mainApp.status == .enabled
 let preview:Bool
 var visible=false
 var openSettings:(()->Void)?
 private var checking=false
 private var trafficChecking=false
 private var probing=false
 private var timer:Timer?
 private var trafficTimer:Timer?
 private var lastProbe=Date.distantPast
 private var counter:(rx:Double,tx:Double,time:Double)?
 private var helperUpdate=HelperUpdatePolicy()
 private var helperUpdateError:String?
 private var helperBuild:String? { Bundle.main.object(forInfoDictionaryKey:"FennecHelperBuild") as? String }
 init() {
  preview=ProcessInfo.processInfo.arguments.contains("--preview") || (Bundle.main.object(forInfoDictionaryKey:"FennecPreview") as? Bool == true)
  if preview {
   installed=true
   if !ProcessInfo.processInfo.arguments.contains("--preview-empty") {
    let server=Server(id:"sample",name:L("Пример сервера"),flag:"🌐",country_code:"",address:"192.0.2.1",port:443,tls:TLSInfo(server_name:"example.invalid"),hasUUID:true,hasPin:false)
    configuration.nodes=[server]
    if ProcessInfo.processInfo.arguments.contains("--preview-many") {
     let codes=["DE","FR","NL","SG","GB","US","JP","FI","SE","CA","AU","CH"]
     configuration.nodes=codes.enumerated().map { index,code in
      Server(id:"sample\(index)",name:L("Сервер {0} — длинное название узла", index+1),flag:"",country_code:code,address:"192.0.2.\(index+1)",port:443,tls:TLSInfo(server_name:"example.invalid"),hasUUID:true,hasPin:false)
     }
    }
    var group=RouteGroup.empty();group.title=L("Рабочие сервисы");group.domains=["example.invalid"];group.nodes=Array(configuration.nodes.prefix(3)).map{$0.id};group.enabled=true
    configuration.groups=[group]
    paths=configuration.nodes.enumerated().map { index,node in Reachability(id:node.id,name:node.name,flag:node.symbol,success:true,latency:Double(12+index*7)) }
    state="on";history=[1000,9000,3000,12000,5000,15000,8000]
   }
   if ProcessInfo.processInfo.arguments.contains("--preview-error") { state="error";if !configuration.groups.isEmpty { configuration.groups[0].killSwitch=true };error=L("Сервер недоступен. Защита сохраняется.") }
   if ProcessInfo.processInfo.arguments.contains("--preview-update") { needsUpdate=true }
   if ProcessInfo.processInfo.arguments.contains("--preview-updating") { needsUpdate=true;busy="install" }
   if ProcessInfo.processInfo.arguments.contains("--preview-recovery") { recoveryRequired=true;state="blocked";error=L("Не удалось восстановить предыдущее состояние. Защита сохранена. Восстановите помощник через меню.") }
   if ProcessInfo.processInfo.arguments.contains("--preview-profile") {
    profileImport=ProfileImport(archive:"",revision:0,preview:ProfilePreview(digest:"",servers:3,groups:4,snapshots:2,killSwitch:true))
   }
   return
  }
  helperUpdate.attemptedBuild=UserDefaults.standard.string(forKey:"helperUpdateAttemptedBuild")
  refresh()
  timer=Timer.scheduledTimer(withTimeInterval:3,repeats:true) { [weak self] _ in
   guard let self,self.visible,self.busy == nil else { return };self.refresh()
   if Date().timeIntervalSince(self.lastProbe)>10 { self.refreshMetrics() }
  }
  trafficTimer=Timer.scheduledTimer(withTimeInterval:0.8,repeats:true) { [weak self] _ in
   guard let self,self.visible,self.installed,self.busy == nil,!self.needsUpdate,!self.trafficChecking else { return }
   self.trafficChecking=true
   bridgeRequest(["action":"traffic"]) { reply in self.trafficChecking=false;if let value=reply.metrics { self.ingest(value) } }
  }
  NSWorkspace.shared.notificationCenter.addObserver(forName:NSWorkspace.didWakeNotification,object:nil,queue:.main) { [weak self] _ in self?.refresh() }
 }
 func refresh() {
  guard !preview,!checking,busy == nil else { return };checking=true
  bridgeRequest(["action":"status"]) { [weak self] reply in
   guard let self else { return };self.checking=false
   self.installed=reply.installed ?? false;self.needsUpdate=(reply.requiresUpdate ?? false) || (reply.ok && self.installed && reply.helperBuild != self.helperBuild);self.state=reply.state ?? "off"
   self.recoveryRequired=reply.recoveryRequired ?? false
   if let value=reply.configuration { self.configuration=value }
   self.error=self.needsUpdate ? (self.helperUpdateError ?? reply.error):reply.error
   if reply.updateState == "updating" {
    self.busy="install";self.pollHelperUpdate(until:Date().addingTimeInterval(240));return
   }
   if self.helperUpdate.begin(installed:self.installed,needsUpdate:self.needsUpdate,build:self.helperBuild,busy:self.busy != nil) {
    UserDefaults.standard.set(self.helperUpdate.attemptedBuild,forKey:"helperUpdateAttemptedBuild")
    if reply.signedUpdates == true { self.updateHelper() }
    else { self.privileged("install") }
   }
  }
 }
 func updateHelper() {
  guard !preview,busy == nil else { return }
  busy="install";error=nil;helperUpdateError=nil;authorizingHelper=false
  bridgeRequest(["action":"update-helper"]) { reply in
   if reply.ok { self.pollHelperUpdate(until:Date().addingTimeInterval(240)) }
   else { self.finishHelperUpdate(error:reply.error ?? L("Не удалось запустить обновление помощника")) }
  }
 }
 private func finishHelperUpdate(error:String?) {
  busy=nil;helperUpdateError=error;self.error=error
  if error == nil { refresh() }
 }
 private func pollHelperUpdate(until deadline:Date) {
  guard !preview else { return }
  DispatchQueue.main.asyncAfter(deadline:.now()+1) { [weak self] in
   guard let self else { return }
   bridgeRequest(["action":"status"]) { reply in
    if reply.updateState == "failed" {
     self.finishHelperUpdate(error:reply.updateError ?? L("Обновление помощника не завершено. Используйте восстановление через меню."))
    } else if reply.ok,reply.helperBuild == self.helperBuild,reply.updateState != "updating" {
     self.finishHelperUpdate(error:nil)
    } else if Date()<deadline { self.pollHelperUpdate(until:deadline) }
    else { self.finishHelperUpdate(error:L("Обновление помощника не завершено. Используйте восстановление через меню.")) }
   }
  }
 }
 func loadEditor(completion:@escaping(Bool)->Void) {
  if preview { completion(true);return }
  bridgeRequest(["action":"configuration"]) { reply in
   if let value=reply.configuration { self.configuration=value }
   if !reply.ok { self.error=reply.error }
   completion(reply.ok)
  }
 }
 func mutate(_ request:[String:Any],completion:@escaping(Reply)->Void = {_ in}) {
  guard busy == nil else { completion(Reply(ok:false,error:L("Другая операция ещё выполняется")));return }
  busy=(request["action"] as? String ?? "")+":"+(request["id"] as? String ?? "")
  if preview {
   busy=nil
   completion(Reply(ok:false,error:L("Preview: применение системных изменений отключено")));return
  }
  bridgeRequest(request) { reply in
   self.busy=nil
   if let value=reply.configuration { self.configuration=value }
   self.error=reply.ok ? nil : reply.error
   completion(reply)
   self.refresh()
  }
 }
 func toggle(_ group:RouteGroup) {
  mutate(["action":"set-enabled","revision":configuration.revision,"id":group.id,"enabled":!group.enabled])
 }
 func toggleProtection(_ group:RouteGroup) {
  mutate(["action":"set-kill-switch","revision":configuration.revision,"id":group.id,"enabled":!group.protected])
 }
 func toggleBypass() {
  mutate(["action":"set-bypass","revision":configuration.revision,"enabled":!configuration.bypassed])
 }
 func editChain(_ group:RouteGroup) { editingChain=group }

 func refreshMetrics() {
  guard !preview,installed,!needsUpdate,!probing else { return };probing=true;lastProbe=Date()
  bridgeRequest(["action":"metrics"]) { reply in
   self.probing=false;if let value=reply.metrics { self.paths=value.paths }
  }
 }
 func ingest(_ value:Metrics) {
  if let old=counter,value.checked>old.time,value.rx>=old.rx,value.tx>=old.tx {
   rxRate=(value.rx-old.rx)/(value.checked-old.time);txRate=(value.tx-old.tx)/(value.checked-old.time)
   history.append(rxRate+txRate);if history.count>40 { history.removeFirst(history.count-40) }
  }
  counter=(value.rx,value.tx,value.checked)
 }
 func privileged(_ action:String) {
  guard !preview,busy == nil else { return };busy=action;error=nil;helperUpdateError=nil;authorizingHelper=true
  let runtime=HelperRuntime(resources:Bundle.main.resourceURL!)
  guard let command=runtime.privilegedCommand(action:action,uid:getuid()) else { busy=nil;error=L("Некорректная операция установки");return }
  let escaped=command.replacingOccurrences(of:"\\",with:"\\\\").replacingOccurrences(of:"\"",with:"\\\"")
  DispatchQueue.global(qos:.userInitiated).async {
   var scriptError:NSDictionary?
   let result=NSAppleScript(source:"do shell script \"\(escaped)\" with administrator privileges")?.executeAndReturnError(&scriptError)
   let reply=result?.stringValue.flatMap{$0.data(using:.utf8)}.flatMap{try? JSONDecoder().decode(Reply.self,from:$0)}
   DispatchQueue.main.async {
    self.busy=nil;self.authorizingHelper=false
    if let reply,reply.ok { self.refresh() }
    else {
     self.helperUpdateError=reply?.error ?? L("Обновление помощника не завершено. Повторите через меню, когда будет доступ администратора.")
     self.error=self.helperUpdateError
    }
   }
  }
 }
 func toggleLogin() {
  guard !preview else { return }
  do {
   if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() }
   else { try SMAppService.mainApp.register() }
   launchAtLogin=SMAppService.mainApp.status == .enabled
  } catch { self.error=L("Не удалось изменить автозапуск") }
 }
}
func formatRate(_ value:Double)->String {
 if value>=1_000_000 { return String(format:L("%.1f МБ/с"),value/1_000_000) }
 if value>=1_000 { return String(format:L("%.0f КБ/с"),value/1_000) }
 return L("{0} Б/с", Int(value))
}
