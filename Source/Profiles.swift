import AppKit
import UniformTypeIdentifiers
import Darwin

extension Center {
 func exportProfile() {
  guard !preview,busy == nil,!needsUpdate else { return }
  let warning=NSAlert();warning.messageText=L("Экспортировать настройки?")
  warning.informativeText=L("Файл содержит ключи доступа к серверам (UUID и TLS pins). Он не зашифрован. Храните его в защищённом месте и не публикуйте.")
  warning.addButton(withTitle:L("Выбрать файл…"));warning.addButton(withTitle:L("Отменить"))
  guard warning.runModal() == .alertFirstButtonReturn else { return }
  let panel=NSSavePanel();panel.nameFieldStringValue="libvpn.libvpn"
  panel.allowedContentTypes=[UTType(filenameExtension:"libvpn") ?? .data,UTType(filenameExtension:"fennec") ?? .data];panel.canCreateDirectories=true
  guard panel.runModal() == .OK,let url=panel.url else { return }
  busy="export-profile";error=nil
  bridgeRequest(["action":"export-profile"]) { reply in
   self.busy=nil
   guard reply.ok,let encoded=reply.archive,let data=Data(base64Encoded:encoded) else { self.error=reply.error ?? L("Не удалось создать профиль");return }
   do { try ProfileFile.write(data,to:url) }
   catch { self.error=L("Не удалось сохранить профиль. Выберите доступный для записи каталог.") }
  }
 }
 func importProfile() {
  guard !preview,busy == nil,!needsUpdate else { return }
  let panel=NSOpenPanel();panel.allowedContentTypes=[UTType(filenameExtension:"libvpn") ?? .data,UTType(filenameExtension:"fennec") ?? .data]
  panel.canChooseDirectories=false;panel.allowsMultipleSelection=false
  guard panel.runModal() == .OK,let url=panel.url else { return }
  let data:Data
  do { data=try ProfileFile.read(url) }
  catch { self.error=L("Не удалось прочитать профиль. Максимальный размер — 32 МБ.");return }
  let encoded=data.base64EncodedString(),revision=configuration.revision
  busy="preview-profile";error=nil
  bridgeRequest(["action":"preview-profile","revision":revision,"archive":encoded]) { reply in
   self.busy=nil
   if reply.ok,let summary=reply.profile { self.profileImport=ProfileImport(archive:encoded,revision:revision,preview:summary) }
   else { self.error=reply.error ?? L("Не удалось открыть профиль") }
  }
 }
 func applyProfile() {
  guard let draft=profileImport,!draft.saving else { return }
  profileImport?.saving=true;profileImport?.error=nil
  mutate(["action":"import-profile","revision":draft.revision,"archive":draft.archive,"digest":draft.preview.digest]) { reply in
   if reply.ok { self.profileImport=nil }
   else { self.profileImport?.saving=false;self.profileImport?.error=reply.error ?? L("Не удалось применить профиль") }
  }
 }
}
