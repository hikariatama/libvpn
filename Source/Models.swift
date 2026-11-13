import Foundation

struct TLSInfo: Codable, Equatable {
 var enabled:Bool = true
 var server_name:String
 var min_version:String?
 var max_version:String?
}
struct Server: Codable, Identifiable, Equatable {
 var id:String
 var name:String
 var flag:String
 var country_code:String
 var address:String?
 var port:Int?
 var tls:TLSInfo?
 var hasUUID:Bool?
 var hasPin:Bool?
}
extension Server {
 var symbol:String {
  if country_code.count==2, country_code.unicodeScalars.allSatisfy({(65...90).contains(Int($0.value)) || (97...122).contains(Int($0.value))}) {
   return String(String.UnicodeScalarView(country_code.uppercased().unicodeScalars.compactMap { UnicodeScalar(127397+$0.value) }))
  }
  return flag.isEmpty ? "🌐":flag
 }
}
struct RuleSet: Codable, Identifiable, Equatable {
 var kind:String
 var name:String
 var digest:String
 var url:String?
 var id:String { digest }
 var updatable:Bool { kind != "custom" || url != nil }
 var kindTitle:String { kind=="geoip" ? "GeoIP":kind=="geosite" ? "Geosite":L("Свой") }
 var payload:[String:Any] {
  var value:[String:Any] = ["kind":kind,"name":name,"digest":digest]
  if let url { value["url"]=url }
  return value
 }
}
struct RuleSetContents: Decodable {
 var domains:[String]
 var suffixes:[String]
 var keywords:[String]
 var regexes:[String]
 var cidrs:[String]
}
struct RouteGroup: Codable, Identifiable, Equatable {
 var id:String
 var title:String
 var flag:String
 var enabled:Bool
 var killSwitch:Bool?
 var protected:Bool { killSwitch ?? false }
 var nodes:[String]
 var domains:[String]?
 var suffixes:[String]?
 var keywords:[String]?
 var regexes:[String]?
 var cidrs:[String]?
 var ruleSets:[RuleSet]?
 var exit:String?
 var overrideAddress:String?
 var throughTailnet:Bool { exit=="tailnet" }
 static func empty()->RouteGroup {
  RouteGroup(id:UUID().uuidString.replacingOccurrences(of:"-",with:""),title:"",flag:"",enabled:false,nodes:[],domains:[],suffixes:[],keywords:[],regexes:[],cidrs:[],ruleSets:[])
 }
 var displayTitle:String { id=="fallback" && ["Остальной трафик","Other Traffic"].contains(title) ? L("Остальной трафик"):title }
 var payload:[String:Any] {
  var value:[String:Any] = ["id":id,"title":title,"flag":flag,"enabled":enabled,"killSwitch":protected,"nodes":nodes]
  if id != "fallback" {
   value["domains"]=domains ?? [];value["suffixes"]=suffixes ?? []
   value["keywords"]=keywords ?? [];value["regexes"]=regexes ?? []
   value["cidrs"]=cidrs ?? [];value["ruleSets"]=(ruleSets ?? []).map(\.payload)
   if throughTailnet { value["exit"]="tailnet";value["nodes"]=[String]() }
   if let overrideAddress,!overrideAddress.isEmpty { value["overrideAddress"]=overrideAddress }
  }
  return value
 }
}
struct Configuration: Codable, Equatable {
 var schemaVersion:Int
 var revision:Int
 var nodes:[Server]
 var groups:[RouteGroup]
 var fallback:RouteGroup
 var killSwitch:Bool
 var bypass:Bool?
 var remoteAccess:RemoteAccess?
 var tailnet:Tailnet?
 var bypassed:Bool { bypass ?? false }
 var routes:[RouteGroup] { groups+[fallback] }
 static var empty:Configuration {
  Configuration(schemaVersion:10,revision:0,nodes:[],groups:[],fallback:RouteGroup(id:"fallback",title:L("Остальной трафик"),flag:"🌐",enabled:false,nodes:[]),killSwitch:false)
 }
}
struct RemoteAccess:Codable,Equatable {
 var enabled:Bool
 var route:String
 var localPort:Int
 var address:String?
 var port:Int?
 var serverPublicKey:String?
 var tunnelAddress:String?
 var peerAddress:String?
 var publicKey:String?
}
struct RemoteDraft:Equatable {
 var address=""
 var port=""
 var serverPublicKey=""
 var peerAddress=""
 var tunnelAddress=""
 var localPort=""
 var route=""
 var enabled=true
 init(_ remote:RemoteAccess?=nil) {
  guard let remote else { return }
  address=remote.address ?? "";port=remote.port.map(String.init) ?? ""
  serverPublicKey=remote.serverPublicKey ?? "";peerAddress=remote.peerAddress ?? ""
  tunnelAddress=remote.tunnelAddress ?? "";localPort=String(remote.localPort)
  route=remote.route;enabled=remote.enabled
 }
 func payload(rotateKeys:Bool=false)->[String:Any] {
  ["enabled":enabled,"address":address.trimmingCharacters(in:.whitespaces),"port":Int(port) ?? 0,
   "serverPublicKey":serverPublicKey.trimmingCharacters(in:.whitespacesAndNewlines),
   "tunnelAddress":tunnelAddress.trimmingCharacters(in:.whitespaces),"peerAddress":peerAddress.trimmingCharacters(in:.whitespaces),
   "localPort":Int(localPort) ?? 0,"route":route,"rotateKeys":rotateKeys]
 }
}
struct TailnetService:Codable,Equatable {
 var proto:String
 var port:Int
 var localPort:Int
 enum CodingKeys:String,CodingKey { case proto="protocol",port,localPort }
}
struct Tailnet:Codable,Equatable {
 var enabled:Bool
 var route:String
 var controlURL:String
 var hostname:String
 var ephemeral:Bool?
 var acceptRoutes:Bool?
 var listenPort:Int?
 var services:[TailnetService]?
 var hasAuthKey:Bool?
}
struct TailnetPortDraft:Identifiable,Equatable {
 var id=UUID()
 var proto="tcp"
 var port=""
 var localPort=""
}
struct TailnetDraft:Equatable {
 var enabled=true
 var controlURL=""
 var hostname=""
 var authKey=""
 var clearAuthKey=false
 var hasAuthKey=false
 var ephemeral=false
 var acceptRoutes=false
 var route=""
 var listenPort="41641"
 var ports:[TailnetPortDraft]=[]
 init(_ value:Tailnet?=nil) {
  guard let value else { return }
  enabled=value.enabled;controlURL=value.controlURL;hostname=value.hostname;hasAuthKey=value.hasAuthKey ?? false
  ephemeral=value.ephemeral ?? false;acceptRoutes=value.acceptRoutes ?? false;route=value.route
  listenPort=String(value.listenPort ?? 41641)
  ports=(value.services ?? []).map{TailnetPortDraft(proto:$0.proto,port:String($0.port),localPort:String($0.localPort))}
 }
 // Edited port rows keep their identity, so compare only what is sent.
 static func == (a:TailnetDraft,b:TailnetDraft)->Bool { NSDictionary(dictionary:a.payload()).isEqual(to:b.payload()) }
 func payload(resetLogin:Bool=false)->[String:Any] {
  var value:[String:Any]=["enabled":enabled,"controlURL":controlURL.trimmingCharacters(in:.whitespaces),
   "hostname":hostname.trimmingCharacters(in:.whitespaces).lowercased(),"ephemeral":ephemeral,"acceptRoutes":acceptRoutes,
   "route":route,"listenPort":Int(listenPort.trimmingCharacters(in:.whitespaces)) ?? 0,"resetLogin":resetLogin,
   "services":ports.map{["protocol":$0.proto,"port":Int($0.port.trimmingCharacters(in:.whitespaces)) ?? 0,
                         "localPort":Int(($0.localPort.isEmpty ? $0.port:$0.localPort).trimmingCharacters(in:.whitespaces)) ?? 0]}]
  // The stored key is never sent back: omit it to keep, send "" to clear.
  let key=authKey.trimmingCharacters(in:.whitespacesAndNewlines)
  if !key.isEmpty { value["authKey"]=key } else if clearAuthKey { value["authKey"]="" }
  return value
 }
}
func wireGuardPeer(publicKey:String,tunnelAddress:String)->String {
 "[Peer]\nPublicKey = \(publicKey)\nAllowedIPs = \(tunnelAddress)\(tunnelAddress.contains(":") ? "/128":"/32")\n"
}
enum AppStatus:Equatable {
 case needsHelper,updating,failure,bypass,needsServer,starting,blocked,off
 case active(routes:Int,total:Int)
}
func appStatus(installed:Bool,needsUpdate:Bool,state:String,configuration:Configuration)->AppStatus {
 if !installed { return .needsHelper }
 if needsUpdate { return .updating }
 if state=="error" || state=="blocked" { return .failure }
 if configuration.bypassed { return .bypass }
 if configuration.nodes.isEmpty { return .needsServer }
 let routes=configuration.routes
 let enabled=routes.filter(\.enabled).count
 if enabled>0 { return state=="on" ? .active(routes:enabled,total:routes.count):.starting }
 return routes.contains(where:\.protected) ? (state=="on" ? .blocked:.starting):.off
}
struct Reachability:Decodable,Identifiable {
 let id:String
 let name:String
 let flag:String
 let success:Bool
 let latency:Double?
}
struct Metrics:Decodable {
 let checked:Double
 let probed:Bool
 let paths:[Reachability]
 let rx:Double
 let tx:Double
}
struct ProfilePreview:Decodable {
 let digest:String
 let servers:Int
 let groups:Int
 let snapshots:Int
 let killSwitch:Bool
 var bypass:Bool?
 var remoteAccess:Bool?
 var tailnet:Bool?
}
struct ProfileImport:Identifiable {
 let id=UUID()
 let archive:String
 let revision:Int
 let preview:ProfilePreview
 var saving=false
 var error:String?
}
struct Reply:Decodable {
 let ok:Bool
 var installed:Bool?
 var version:Int?
 var requiresUpdate:Bool?
 var recoveryRequired:Bool?
 var signedUpdates:Bool?
 var updateAccepted:Bool?
 var updateState:String?
 var updateError:String?
 var helperBuild:String?
 var state:String?
 var error:String?
 var remoteState:String?
 var tailnetState:String?
 var configuration:Configuration?
 var metrics:Metrics?
 var profile:ProfilePreview?
 var archive:String?
 var ruleSet:RuleSet?
 var contents:RuleSetContents?
}
struct EditorSession<Value:Equatable> {
 var original:Value
 var draft:Value
 var revision:Int
 var saving=false
 var error:String?
 var changed:Bool { original != draft }
 mutating func finish(ok:Bool,error:String?)->Bool {
  saving=false;self.error=ok ? nil : (error ?? L("Не удалось сохранить изменения"))
  return ok
 }
}
struct ServerDraft:Identifiable,Equatable {
 var id=UUID().uuidString.replacingOccurrences(of:"-",with:"")
 var name=""
 var flag=""
 var country=""
 var address=""
 var port="443"
 var uuid=""
 var serverName=""
 var minTLS=""
 var maxTLS=""
 var pin=""
 var clearPin=false
 var hasUUID=false
 var hasPin=false
 var uri=""
 init() {}
 init(_ server:Server) {
  id=server.id;name=server.name;flag=server.flag;country=server.country_code
  address=server.address ?? "";port=String(server.port ?? 443);serverName=server.tls?.server_name ?? ""
  minTLS=server.tls?.min_version ?? "";maxTLS=server.tls?.max_version ?? ""
  hasUUID=server.hasUUID ?? true;hasPin=server.hasPin ?? false
 }
 var payload:[String:Any] {
  if !uri.isEmpty { return ["uri":uri] }
  var tls:[String:Any] = ["enabled":true,"server_name":serverName.isEmpty ? address : serverName]
  if !minTLS.isEmpty { tls["min_version"]=minTLS }
  if !maxTLS.isEmpty { tls["max_version"]=maxTLS }
  if !pin.isEmpty { tls["certificate_public_key_sha256"]=[pin] }
  return ["id":id,"name":name,"flag":flag,"country_code":country,"address":address,
          "port":Int(port) ?? 0,"uuid":uuid,"tls":tls,"protocol":"vless","transport":"tcp","clearPin":clearPin]
 }
}
func lines(_ text:String)->[String] {
 text.split(whereSeparator:{$0.isNewline}).map{String($0).trimmingCharacters(in:.whitespaces)}.filter{!$0.isEmpty}
}
func mergedLines(_ text:String,_ values:[String])->String {
 var seen=Set<String>()
 return (lines(text)+values).filter{seen.insert($0).inserted}.joined(separator:"\n")
}
func moveNode(_ source:String,over target:String,in nodes:[String])->[String] {
 guard let from=nodes.firstIndex(of:source),let to=nodes.firstIndex(of:target),from != to else { return nodes }
 var result=nodes;result.remove(at:from);result.insert(source,at:to);return result
}

func chainSymbols(_ ids:[String],servers:[Server])->String {
 let registry=Dictionary(uniqueKeysWithValues:servers.map{($0.id,$0)})
 return ids.isEmpty ? "—":ids.map{registry[$0]?.symbol ?? "?"}.joined(separator:" › ")
}
