import Foundation
import Darwin

enum ProfileFile {
 static let maxBytes=32*1024*1024
 static func read(_ url:URL)throws->Data {
  let handle=try FileHandle(forReadingFrom:url);defer { try? handle.close() }
  let data=try handle.read(upToCount:maxBytes+1) ?? Data()
  guard data.count<=maxBytes else { throw CocoaError(.fileReadTooLarge) }
  return data
 }
 static func write(_ data:Data,to url:URL)throws {
  guard data.count<=maxBytes else { throw CocoaError(.fileWriteOutOfSpace) }
  let temp=url.deletingLastPathComponent().appendingPathComponent(".fennec-"+UUID().uuidString+".tmp")
  let fd=Darwin.open(temp.path,O_WRONLY|O_CREAT|O_EXCL,mode_t(0o600))
  guard fd>=0 else { throw CocoaError(.fileWriteNoPermission) }
  defer { try? FileManager.default.removeItem(at:temp) }
  let handle=FileHandle(fileDescriptor:fd,closeOnDealloc:true)
  do { try handle.write(contentsOf:data);try handle.synchronize();try handle.close() }
  catch { try? handle.close();throw error }
  guard Darwin.rename(temp.path,url.path)==0 else { throw CocoaError(.fileWriteUnknown) }
 }
}
