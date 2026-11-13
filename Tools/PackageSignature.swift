import Foundation
import CryptoKit

@main struct PackageSignature {
 static func main() {
  do {
   let args=Array(CommandLine.arguments.dropFirst())
   guard let operation=args.first else { throw Failure.invalid }
   switch operation {
   case "generate":
    guard args.count==3,!FileManager.default.fileExists(atPath:args[1]),!FileManager.default.fileExists(atPath:args[2]) else { throw Failure.invalid }
    let key=Curve25519.Signing.PrivateKey()
    guard FileManager.default.createFile(atPath:args[1],contents:key.rawRepresentation,attributes:[.posixPermissions:0o600]) else { throw Failure.invalid }
    try key.publicKey.rawRepresentation.base64EncodedString().write(toFile:args[2],atomically:true,encoding:.utf8)
   case "sign":
    guard args.count==4 else { throw Failure.invalid }
    let key=try Curve25519.Signing.PrivateKey(rawRepresentation:Data(contentsOf:URL(fileURLWithPath:args[1])))
    let signature=try key.signature(for:Data(contentsOf:URL(fileURLWithPath:args[2])))
    try signature.base64EncodedString().write(toFile:args[3],atomically:true,encoding:.utf8)
   case "verify":
    guard args.count==4,
     let publicKey=Data(base64Encoded:try String(contentsOfFile:args[1],encoding:.utf8).trimmingCharacters(in:.whitespacesAndNewlines)),
     let signature=Data(base64Encoded:try String(contentsOfFile:args[3],encoding:.utf8).trimmingCharacters(in:.whitespacesAndNewlines)) else { throw Failure.invalid }
    let key=try Curve25519.Signing.PublicKey(rawRepresentation:publicKey)
    guard key.isValidSignature(signature,for:try Data(contentsOf:URL(fileURLWithPath:args[2]))) else { throw Failure.invalid }
   default: throw Failure.invalid
   }
  } catch { FileHandle.standardError.write(Data("Package signature rejected\n".utf8));exit(1) }
 }
 enum Failure:Error { case invalid }
}
