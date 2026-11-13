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
 var cidrs:[String]?
 var snapshots:[String]?
 static func empty()->RouteGroup {
  RouteGroup(id:UUID().uuidString.replacingOccurrences(of:"-",with:""),title:"",flag:"",enabled:false,nodes:[],domains:[],suffixes:[],cidrs:[],snapshots:[])
 }
 var displayTitle:String { id=="fallback" && ["Остальной трафик","Other Traffic"].contains(title) ? L("Остальной трафик"):title }
 var payload:[String:Any] {
  var value:[String:Any] = ["id":id,"title":title,"flag":flag,"enabled":enabled,"killSwitch":protected,"nodes":nodes]
  if id != "fallback" {
   value["domains"]=domains ?? [];value["suffixes"]=suffixes ?? []
   value["cidrs"]=cidrs ?? [];value["snapshots"]=snapshots ?? []
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
 var bypassed:Bool { bypass ?? false }
 static var empty:Configuration {
  Configuration(schemaVersion:9,revision:0,nodes:[],groups:[],fallback:RouteGroup(id:"fallback",title:L("Остальной трафик"),flag:"🌐",enabled:false,nodes:[]),killSwitch:false)
 }
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
 var configuration:Configuration?
 var metrics:Metrics?
 var profile:ProfilePreview?
 var archive:String?
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
func moveNode(_ source:String,over target:String,in nodes:[String])->[String] {
 guard let from=nodes.firstIndex(of:source),let to=nodes.firstIndex(of:target),from != to else { return nodes }
 var result=nodes;result.remove(at:from);result.insert(source,at:to);return result
}

func chainSymbols(_ ids:[String],servers:[Server])->String {
 let registry=Dictionary(uniqueKeysWithValues:servers.map{($0.id,$0)})
 return ids.isEmpty ? "—":ids.map{registry[$0]?.symbol ?? "?"}.joined(separator:" › ")
}
