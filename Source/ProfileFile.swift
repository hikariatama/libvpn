import Foundation
import Darwin

enum ProfileFile {
 static let maxBytes=32*1024*1024
 static func read(_ url:URL)throws->Data {
  let fd=Darwin.open(url.path,O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC)
  guard fd>=0 else { throw CocoaError(.fileReadUnknown) }
  defer { Darwin.close(fd) }
  var info=stat()
  guard fstat(fd,&info)==0,info.st_mode & S_IFMT == S_IFREG else { throw CocoaError(.fileReadUnsupportedScheme) }
  guard info.st_size>=0,info.st_size<=maxBytes else { throw CocoaError(.fileReadTooLarge) }
  var data=Data(),buffer=[UInt8](repeating:0,count:65536)
  let deadline=ProcessInfo.processInfo.systemUptime+8
  while true {
   guard ProcessInfo.processInfo.systemUptime<=deadline else { throw CocoaError(.fileReadUnknown) }
   let count=Darwin.read(fd,&buffer,min(buffer.count,maxBytes+1-data.count))
   if count<0 {
    if errno == EINTR { continue }
    throw CocoaError(.fileReadUnknown)
   }
   if count==0 { break }
   data.append(contentsOf:buffer.prefix(count))
   guard data.count<=maxBytes else { throw CocoaError(.fileReadTooLarge) }
  }
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
