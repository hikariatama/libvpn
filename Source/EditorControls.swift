import SwiftUI
import UniformTypeIdentifiers

struct EditorActionStyle:ViewModifier {
 var prominent=false
 @ViewBuilder func body(content:Content)->some View {
   if prominent { content.buttonStyle(.borderedProminent) }
   else { content.buttonStyle(.bordered) }
 }
}
struct EditorPane<Content:View>:View {
 let saving:Bool
 let changed:Bool
 let error:String?
 let cancel:()->Void
 let save:()->Void
 let delete:(()->Void)?
 let deleteTitle:String
 @ViewBuilder var content:Content
 var body:some View {
  VStack(spacing:0) {
   content.frame(minWidth:0,maxWidth:.infinity,maxHeight:.infinity).disabled(saving)
   Divider()
   VStack(alignment:.leading,spacing:8) {
    if let error { Text(error).font(.callout).foregroundStyle(.red).fixedSize(horizontal:false,vertical:true).textSelection(.enabled) }
    HStack(spacing:8) {
     if let delete { Button(deleteTitle,role:.destructive,action:delete).modifier(EditorActionStyle()) }
     Spacer()
     Button(L("Отменить"),action:cancel).keyboardShortcut(.cancelAction).modifier(EditorActionStyle()).disabled(!changed)
     Button(action:save) {
      HStack(spacing:6) {
       if saving { ProgressView().controlSize(.mini) }
       Text(L("Сохранить"))
      }
     }.keyboardShortcut(.defaultAction).modifier(EditorActionStyle(prominent:true)).disabled(!changed)
    }.disabled(saving)
   }.padding(12)
  }
 }
}
struct CountryPicker:View {
 @Binding var selection:String
 static let codes=Locale.Region.isoRegions.map(\.identifier).filter{$0.count==2}.sorted {
  (Locale(identifier:AppLanguage.code).localizedString(forRegionCode:$0) ?? $0) < (Locale(identifier:AppLanguage.code).localizedString(forRegionCode:$1) ?? $1)
 }
 func label(_ code:String)->String {
  let flag=String(String.UnicodeScalarView(code.unicodeScalars.compactMap { UnicodeScalar(127397+$0.value) }))
  return flag+" "+(Locale(identifier:AppLanguage.code).localizedString(forRegionCode:code) ?? code)
 }
 var body:some View {
  Picker(L("Страна"),selection:$selection) {
   Text(L("Не указана")).tag("")
   if !selection.isEmpty && !Self.codes.contains(selection) { Text(selection).tag(selection) }
   ForEach(Self.codes,id:\.self) { code in Text(label(code)).tag(code) }
  }.accessibilityLabel(L("Страна"))
 }
}
struct RuleSetSection:View {
 @ObservedObject var model:Center
 @Binding var ruleSets:[RuleSet]
 @Binding var error:String?
 let expand:(RuleSetContents)->Void
 @State private var source="geosite"
 @State private var category=""
 @State private var country=""
 @State private var url=""
 @State private var working:String?
 var canAdd:Bool {
  switch source { case "geoip":return !country.isEmpty;case "url":return !url.isEmpty;case "file":return true;default:return !category.trimmingCharacters(in:.whitespaces).isEmpty }
 }
 func add(_ request:[String:Any],clear:@escaping()->Void) {
  working="add";error=nil
  model.prepareRuleSet(request) { reply in
   working=nil
   guard reply.ok,let item=reply.ruleSet else { error=reply.error ?? L("Не удалось добавить набор правил");return }
   if !ruleSets.contains(where:{$0.digest==item.digest}) { ruleSets.append(item) }
   clear()
  }
 }
 func chooseFile() {
  let panel=NSOpenPanel();panel.allowedContentTypes=[UTType(filenameExtension:"srs") ?? .data,.json]
  panel.canChooseDirectories=false;panel.allowsMultipleSelection=false
  guard panel.runModal() == .OK,let url=panel.url else { return }
  guard let data=try? ProfileFile.read(url) else { error=L("Не удалось прочитать файл. Максимальный размер — 32 МБ.");return }
  add(["kind":"custom","name":url.deletingPathExtension().lastPathComponent,"data":data.base64EncodedString()]) {}
 }
 func submit() {
  switch source {
  case "file": chooseFile()
  case "url":
   let name=URL(string:url)?.deletingPathExtension().lastPathComponent ?? ""
   add(["kind":"custom","name":name.isEmpty || name=="/" ? url:name,"url":url]) { url="" }
  case "geoip": add(["kind":"geoip","name":country.lowercased()]) { country="" }
  default: add(["kind":"geosite","name":category.trimmingCharacters(in:.whitespaces).lowercased()]) { category="" }
  }
 }
 func listConditions(_ item:RuleSet) {
  working=item.digest;error=nil
  model.inspectRuleSet(item.digest) { reply in
   working=nil
   guard reply.ok,let contents=reply.contents else { error=reply.error ?? L("Не удалось прочитать набор правил");return }
   expand(contents);ruleSets.removeAll{$0.digest==item.digest}
  }
 }
 func name(_ item:RuleSet)->Binding<String> {
  Binding(get:{item.name},set:{ value in if let index=ruleSets.firstIndex(where:{$0.digest==item.digest}) { ruleSets[index].name=value } })
 }
 var body:some View {
  VStack(alignment:.leading,spacing:8) {
   HStack(spacing:8) {
    Picker(L("Источник набора правил"),selection:$source) {
     Text("Geosite").tag("geosite");Text("GeoIP").tag("geoip");Text(L("Ссылка")).tag("url");Text(L("Файл")).tag("file")
    }.labelsHidden().fixedSize().accessibilityLabel(L("Источник набора правил"))
    if source=="geosite" {
     TextField(L("Категория"),text:$category,prompt:Text("category-example")).labelsHidden().accessibilityLabel(L("Категория"))
      .help(L("Название категории из SagerNet/sing-geosite."))
    } else if source=="geoip" {
     CountryPicker(selection:$country).labelsHidden().help(L("Диапазоны адресов страны из SagerNet/sing-geoip."))
    } else if source=="url" {
     TextField(L("Ссылка"),text:$url,prompt:Text("https://…")).labelsHidden().accessibilityLabel(L("Ссылка"))
      .help(L("Файл .srs или исходник .json для sing-box."))
    } else { Spacer(minLength:0) }
    Button(action:submit) {
     HStack(spacing:6) {
      if working=="add" { ProgressView().controlSize(.mini) }
      Text(source=="file" ? L("Выбрать файл…"):L("Добавить"))
     }
    }.fixedSize().disabled(!canAdd || working != nil).accessibilityLabel(L("Добавить набор правил"))
     .help(source=="file" ? L("Файл .srs или исходник .json для sing-box."):L("Добавить набор правил"))
   }
   ForEach(ruleSets) { item in
    HStack(spacing:8) {
     Text(item.kindTitle).foregroundStyle(.secondary).lineLimit(1).frame(width:52,alignment:.leading)
      .help(item.updatable ? L("Новая версия скачивается через меню «Обновить наборы правил»."):L("Локальная копия. Сама не обновляется."))
     if item.kind=="custom" { TextField(L("Название набора правил"),text:name(item)).labelsHidden().accessibilityLabel(L("Название набора правил")) }
     else { Text(item.name).lineLimit(1).help(item.name).frame(maxWidth:.infinity,alignment:.leading) }
     if item.kind=="custom" {
      Button { listConditions(item) } label:{
       if working==item.digest { ProgressView().controlSize(.mini) } else { Image(systemName:"list.bullet") }
      }.buttonStyle(.borderless).disabled(working != nil)
       .help(L("Заменить набор обычными условиями этого маршрута")).accessibilityLabel(L("Заменить набор обычными условиями этого маршрута"))
     }
     Button { ruleSets.removeAll{$0.digest==item.digest} } label:{Image(systemName:"minus.circle")}
      .buttonStyle(.borderless).disabled(working != nil).help(L("Удалить набор правил")).accessibilityLabel(L("Удалить набор правил"))
    }
   }
  }
 }
}
struct ChainDropDelegate:DropDelegate {
 let target:String
 @Binding var dragging:String?
 @Binding var nodes:[String]
 func validateDrop(info:DropInfo)->Bool { dragging != nil }
 func dropEntered(info:DropInfo) {
  if let dragging { nodes=moveNode(dragging,over:target,in:nodes) }
 }
 func dropUpdated(info:DropInfo)->DropProposal? { DropProposal(operation:.move) }
 func performDrop(info:DropInfo)->Bool { dragging=nil;return true }
}
struct EditorChain:View {
 let servers:[Server]
 @Binding var nodes:[String]
 @State private var dragging:String?
 @State private var selected:String?
 var selectedIndex:Int? { selected.flatMap{nodes.firstIndex(of:$0)} }
 func move(_ offset:Int) {
  guard let index=selectedIndex,nodes.indices.contains(index+offset) else { return }
  nodes.swapAt(index,index+offset)
 }
 var body:some View {
  VStack(alignment:.leading,spacing:8) {
   List(selection:$selected) {
    ForEach(nodes,id:\.self) { id in
     let node=servers.first{$0.id==id}
     HStack(spacing:8) {
      Image(systemName:"line.3.horizontal").foregroundStyle(.tertiary).accessibilityHidden(true)
      Text(node?.symbol ?? "🌐").frame(width:18)
      Text(node?.name ?? id).lineLimit(1).help(node?.name ?? id)
      Spacer(minLength:0)
     }.tag(id).contentShape(Rectangle())
      .onDrag { dragging=id;selected=id;return NSItemProvider(object:id as NSString) }
      .onDrop(of:[UTType.utf8PlainText.identifier],delegate:ChainDropDelegate(target:id,dragging:$dragging,nodes:$nodes))
    }
   }.listStyle(.bordered).environment(\.defaultMinListRowHeight,26)
    .overlay { if nodes.isEmpty { Text(L("Цепочка пуста")).foregroundStyle(.secondary).allowsHitTesting(false) } }
   HStack(spacing:10) {
    Menu {
     ForEach(servers.filter{!nodes.contains($0.id)}) { node in
      Button(node.symbol+" "+node.name) { nodes.append(node.id);selected=node.id }
     }
    } label:{Label(L("Добавить сервер"),systemImage:"plus")}
     .menuStyle(.borderlessButton).fixedSize().accessibilityLabel(L("Добавить сервер в цепочку"))
     .disabled(servers.allSatisfy{nodes.contains($0.id)})
    Spacer()
    ControlGroup {
     Button { if let selected { nodes.removeAll{$0==selected};self.selected=nil } } label:{Image(systemName:"minus")}
      .disabled(selectedIndex==nil).accessibilityLabel(L("Удалить из цепочки"))
     Button { move(-1) } label:{Image(systemName:"chevron.up")}
      .disabled(selectedIndex==nil || selectedIndex==0).accessibilityLabel(L("Переместить выше"))
     Button { move(1) } label:{Image(systemName:"chevron.down")}
      .disabled(selectedIndex==nil || selectedIndex==nodes.count-1).accessibilityLabel(L("Переместить ниже"))
    }.fixedSize()
   }.controlSize(.small)
  }
 }
}
