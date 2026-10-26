import Foundation

struct HelperUpdatePolicy {
 var attemptedBuild:String?
 mutating func begin(installed:Bool,needsUpdate:Bool,build:String?,busy:Bool)->Bool {
  guard installed,needsUpdate,!busy,let build,!build.isEmpty,attemptedBuild != build else { return false }
  attemptedBuild=build
  return true
 }
}

struct HelperRuntime {
 let resources:URL
 var executable:URL { resources.appendingPathComponent("Python/bin/python3") }
 func arguments(role:String="bridge",extra:[String]=[])->[String] {
  ["-I","-S","-B",resources.appendingPathComponent("launcher.py").path,role]+extra
 }
 func privilegedCommand(action:String,uid:UInt32)->String? {
  guard action == "install" else { return nil }
  let extra=[action,String(uid)]
  return ([executable.path]+arguments(extra:extra)).map { "'"+$0.replacingOccurrences(of:"'",with:"'\\''")+"'" }.joined(separator:" ")
 }
}
