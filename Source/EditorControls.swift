import SwiftUI
import UniformTypeIdentifiers

struct EditorActionStyle:ViewModifier {
 var prominent=false
 @ViewBuilder func body(content:Content)->some View {
   if prominent { content.buttonStyle(.borderedProminent) }
   else { content.buttonStyle(.bordered) }
 }
}
struct EditorFooter:View {
 let saving:Bool
 let error:String?
 let cancel:()->Void
 let save:()->Void
 var body:some View {
  VStack(alignment:.leading,spacing:10) {
   if let error { Text(error).font(.callout).foregroundStyle(.red).fixedSize(horizontal:false,vertical:true).textSelection(.enabled) }
   HStack(spacing:8) {
    Spacer()
    Button(L("Отменить"),action:cancel).keyboardShortcut(.cancelAction).modifier(EditorActionStyle())
    Button(action:save) {
     HStack(spacing:6) {
      if saving { ProgressView().controlSize(.mini) }
      Text(L("Сохранить"))
     }
    }.keyboardShortcut(.defaultAction).modifier(EditorActionStyle(prominent:true))
   }.disabled(saving)
  }
 }
}
struct EditorSheet<Content:View>:View {
 let title:String
 let saving:Bool
 let error:String?
 let cancel:()->Void
 let save:()->Void
 @ViewBuilder var content:Content
 var body:some View {
  VStack(spacing:0) {
   Text(title).font(.headline).frame(maxWidth:.infinity).padding(.top,20).padding(.bottom,4)
   content.frame(maxWidth:.infinity,maxHeight:.infinity).disabled(saving)
   Divider()
   EditorFooter(saving:saving,error:error,cancel:cancel,save:save).padding(16)
  }.background(Color(nsColor:.windowBackgroundColor))
 }
}
struct EditorForm<Content:View>:View {
 @ViewBuilder var content:Content
 var body:some View {
  ScrollView {
   VStack(alignment:.leading,spacing:14) { content }.textFieldStyle(.roundedBorder)
    .font(.system(size:13)).controlSize(.small)
    .padding(.horizontal,24).padding(.vertical,14)
  }
 }
}
enum PreferenceFieldMetrics {
 static let labelWidth:CGFloat=100
 static let inset:CGFloat=112
}
struct PreferenceField<Content:View>:View {
 let title:String
 @ViewBuilder var content:Content
 var body:some View {
  HStack(alignment:.firstTextBaseline,spacing:12) {
   Text(title).frame(width:PreferenceFieldMetrics.labelWidth,alignment:.trailing)
   content.frame(maxWidth:.infinity,alignment:.leading)
  }
 }
}
struct PreferenceSection<Content:View>:View {
 var title:String?
 var alignedToFields=true
 @ViewBuilder var content:Content
 var body:some View {
  VStack(alignment:.leading,spacing:9) {
   if let title {
    Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
     .padding(.leading,alignedToFields ? PreferenceFieldMetrics.inset:0)
   }
   content
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
