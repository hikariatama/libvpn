import Foundation

#if !FENNEC_INSTALL_PAYLOAD
// Model-only builds have no installable payload and must fail closed.
let helperInstallDigest = ""
#endif

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
 func privilegedCommand(action:String,uid:UInt32,digest:String=helperInstallDigest)->String? {
  guard action == "install",uid>0,digest.count==64,digest.allSatisfy({ "0123456789abcdef".contains($0) }) else { return nil }
  func quote(_ value:String)->String { "'"+value.replacingOccurrences(of:"'",with:"'\\''")+"'" }
  let payload=quote(resources.appendingPathComponent("HelperInstall.zip").path)
  let script="""
  set -eu
  umask 077
  stage=$(/usr/bin/mktemp -d /private/tmp/libvpn-install.XXXXXXXX)
  trap '/bin/rm -rf "$stage"' EXIT
  /bin/dd if=\(payload) of="$stage/payload.zip" bs=1048576 count=129 2>/dev/null
  printf '%s  %s\\n' '\(digest)' "$stage/payload.zip" | /usr/bin/shasum -a 256 -c - >/dev/null
  /usr/bin/ditto -x -k "$stage/payload.zip" "$stage/Resources"
  "$stage/Resources/Python/bin/python3" -I -S -B "$stage/Resources/launcher.py" bridge install \(uid)
  """
  return "/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh -c "+quote(script)
 }
}
