import SwiftUI
import AppKit

enum SettingsSection:String,CaseIterable,Identifiable {
 case servers,rules
 var id:String { rawValue }
 var title:String { self == .servers ? L("Серверы"):L("Правила") }
 var symbol:String { self == .servers ? "externaldrive.connected.to.line.below":"arrow.triangle.branch" }
}
final class SettingsController:NSTabViewController {
 private let rememberPane:Bool
 private var preparing=true
 init(model:Center) {
  rememberPane = !model.preview
  super.init(nibName:nil,bundle:nil)
  tabStyle = .toolbar
  canPropagateSelectedChildViewControllerTitle=true
  for section in SettingsSection.allCases {
   let controller=NSHostingController(rootView:SettingsView(model:model,section:section))
   controller.sizingOptions=[]
   controller.title=section.title
   controller.preferredContentSize=NSSize(width:520,height:300)
   let item=NSTabViewItem(viewController:controller)
   item.identifier=section.rawValue;item.label=section.title
   item.image=NSImage(systemSymbolName:section.symbol,accessibilityDescription:section.title)
   addTabViewItem(item)
  }
  if rememberPane,let saved=UserDefaults.standard.string(forKey:"settingsPane"),
   let index=SettingsSection.allCases.firstIndex(where:{$0.rawValue==saved}) { selectedTabViewItemIndex=index }
  preparing=false
 }
 required init?(coder:NSCoder) { fatalError("init(coder:) is unsupported") }
 override func viewDidAppear() {
  super.viewDidAppear()
  view.window?.toolbar?.displayMode = .iconAndLabel
  view.window?.toolbar?.allowsUserCustomization=false
  view.window?.title=tabViewItems[selectedTabViewItemIndex].label
  view.window?.setContentSize(NSSize(width:520,height:300))
 }
 override func tabView(_ tabView:NSTabView,didSelect tabViewItem:NSTabViewItem?) {
  super.tabView(tabView,didSelect:tabViewItem)
  guard let tabViewItem else { return }
  view.window?.title=tabViewItem.label
  if rememberPane,!preparing,let id=tabViewItem.identifier as? String { UserDefaults.standard.set(id,forKey:"settingsPane") }
 }
}
struct SettingsView:View {
 @ObservedObject var model:Center
 let section:SettingsSection
 @State private var selectedServer:String?
 @State private var selectedGroup:String?
 @State private var server:ServerDraft?
 @State private var group:RouteGroup?
 @State private var revision=0
 @State private var deletingServer:Server?
 @State private var deletingGroup:RouteGroup?
 var selectedNode:Server? { model.configuration.nodes.first{$0.id==selectedServer} }
 var selectedRoute:RouteGroup? { (model.configuration.groups+[model.configuration.fallback]).first{$0.id==selectedGroup} }
 var hasSelection:Bool { section == .servers ? selectedNode != nil:selectedRoute != nil }
 func editServer(_ value:Server?) {
  model.loadEditor { ok in
   guard ok else { return }
   revision=model.configuration.revision
   server=value.flatMap{old in model.configuration.nodes.first{$0.id==old.id}}.map{ServerDraft($0)} ?? ServerDraft()
  }
 }
 func editGroup(_ value:RouteGroup) {
  model.loadEditor { ok in
   guard ok else { return }
   revision=model.configuration.revision
   group=(model.configuration.groups+[model.configuration.fallback]).first{$0.id==value.id} ?? value
  }
 }
 func editSelection() {
  if section == .servers { if let selectedNode { editServer(selectedNode) } }
  else if let selectedRoute { editGroup(selectedRoute) }
 }
 func deleteSelection() {
  if section == .servers { deletingServer=selectedNode }
  else if selectedRoute?.id != "fallback" { deletingGroup=selectedRoute }
 }
 func add() { if section == .servers { editServer(nil) } else { editGroup(.empty()) } }
 func moveGroup(_ value:RouteGroup,by offset:Int) {
  var ids=model.configuration.groups.map(\.id)
  guard let index=ids.firstIndex(of:value.id),ids.indices.contains(index+offset) else { return }
  ids.swapAt(index,index+offset)
  model.mutate(["action":"reorder-groups","revision":model.configuration.revision,"ids":ids])
 }
 func moveGroups(from source:IndexSet,to destination:Int) {
  var ids=model.configuration.groups.map(\.id)
  guard source.allSatisfy({$0<ids.count}),destination<=ids.count else { return }
  ids.move(fromOffsets:source,toOffset:destination)
  model.mutate(["action":"reorder-groups","revision":model.configuration.revision,"ids":ids])
 }
 @ViewBuilder var servers:some View {
  if model.configuration.nodes.isEmpty {
   VStack(spacing:12) {
    Text(L("Нет серверов")).foregroundStyle(.secondary)
    Button(L("Добавить сервер…"),action:add).modifier(EditorActionStyle())
   }.frame(maxWidth:.infinity,maxHeight:.infinity)
  } else {
   List(selection:$selectedServer) {
    ForEach(model.configuration.nodes) { node in
     HStack(spacing:9) {
      Text(node.symbol).frame(width:20)
      Text(node.name).lineLimit(1).help(node.name)
      Spacer(minLength:8)
     }.frame(minHeight:27).tag(node.id).contentShape(Rectangle())

    }
   }.listStyle(.inset).onDeleteCommand(perform:deleteSelection)
    .contextMenu(forSelectionType:String.self) { ids in
     if let id=ids.first,let node=model.configuration.nodes.first(where:{$0.id==id}) {
      Button(L("Изменить…")) { editServer(node) }
      Button(L("Удалить…"),role:.destructive) { deletingServer=node }
     } else { Button(L("Добавить сервер…"),action:add) }
    } primaryAction: { ids in
     if let id=ids.first,let node=model.configuration.nodes.first(where:{$0.id==id}) { editServer(node) }
    }
  }
 }
 var rules:some View {
  List(selection:$selectedGroup) {
   ForEach(model.configuration.groups+[model.configuration.fallback]) { item in
    HStack(spacing:9) {
     Text(item.flag.isEmpty ? "↗":item.flag).frame(width:20)
     Text(item.displayTitle).lineLimit(1).help(item.displayTitle)
     Spacer(minLength:10)
     Text(chainSymbols(item.nodes,servers:model.configuration.nodes)).foregroundStyle(.secondary).lineLimit(1)
     if item.protected {
      Image(systemName:"hand.raised.fill").font(.system(size:11)).foregroundStyle(.red).help(L("Kill switch включён"))
     }
    }.frame(minHeight:27).tag(item.id).contentShape(Rectangle())

   }.onMove(perform:moveGroups)
  }.listStyle(.inset).onDeleteCommand(perform:deleteSelection)
   .contextMenu(forSelectionType:String.self) { ids in
    if let id=ids.first,let route=(model.configuration.groups+[model.configuration.fallback]).first(where:{$0.id==id}) {
     Button(L("Изменить…")) { editGroup(route) }
     if route.id != "fallback" {
      Button(L("Выше")) { moveGroup(route,by:-1) }.disabled(id==model.configuration.groups.first?.id)
      Button(L("Ниже")) { moveGroup(route,by:1) }.disabled(id==model.configuration.groups.last?.id)
      Divider()
      Button(L("Удалить…"),role:.destructive) { deletingGroup=route }
     }
    } else { Button(L("Добавить правило…"),action:add) }
   } primaryAction: { ids in
    if let id=ids.first,let route=(model.configuration.groups+[model.configuration.fallback]).first(where:{$0.id==id}) { editGroup(route) }
   }
 }
 var listActions:some View {
  HStack(spacing:12) {
   ControlGroup {
    Button(action:add) { Image(systemName:"plus") }
     .keyboardShortcut("n",modifiers:.command)
     .help(section == .servers ? L("Добавить сервер"):L("Добавить правило"))
     .accessibilityLabel(section == .servers ? L("Добавить сервер"):L("Добавить правило"))
    Button(role:.destructive,action:deleteSelection) { Image(systemName:"minus") }
     .help(L("Удалить выбранное")).accessibilityLabel(L("Удалить выбранное"))
     .disabled(!hasSelection || (section == .rules && selectedGroup=="fallback"))
   }.controlGroupStyle(.automatic)
   Menu {
    if section == .rules,let selectedRoute,selectedRoute.id != "fallback" {
     Button(L("Выше")) { moveGroup(selectedRoute,by:-1) }.disabled(selectedGroup==model.configuration.groups.first?.id)
     Button(L("Ниже")) { moveGroup(selectedRoute,by:1) }.disabled(selectedGroup==model.configuration.groups.last?.id)
     Divider()
    }
    Button(L("Импортировать…")) { model.importProfile() }
    Button(L("Экспортировать…")) { model.exportProfile() }
   } label:{Image(systemName:"ellipsis")}.menuStyle(.borderlessButton)
    .fixedSize().help(L("Действия")).accessibilityLabel(L("Действия"))
   Spacer()
   Button(L("Изменить…"),action:editSelection).disabled(!hasSelection)
  }.controlSize(.small).padding(.horizontal,16).padding(.vertical,10)
 }
 var body:some View {
  VStack(spacing:0) {
   Group { if section == .servers { servers } else { rules } }
    .frame(maxWidth:.infinity,maxHeight:.infinity)
   Divider()
   listActions
   if let error=model.error {
    Text(error).font(.callout).foregroundStyle(.red).fixedSize(horizontal:false,vertical:true)
     .frame(maxWidth:.infinity,alignment:.leading).padding(12)
   }
  }.font(.body).frame(width:520,height:300).background(Color(nsColor:.windowBackgroundColor))
   .disabled(model.busy != nil)
   .sheet(item:$server) { draft in ServerEditor(model:model,session:EditorSession(original:draft,draft:draft,revision:revision)) }
   .sheet(item:$group) { value in GroupEditor(model:model,group:value,revision:revision) }
   .sheet(isPresented:Binding(get:{model.profileImport != nil},set:{if !$0 && model.profileImport?.saving != true { model.profileImport=nil }})) {
    ProfileImportSheet(model:model)
   }
   .alert(L("Удалить сервер?"),isPresented:Binding(get:{deletingServer != nil},set:{if !$0 { deletingServer=nil }})) {
    Button(L("Отменить"),role:.cancel) { deletingServer=nil }
    Button(L("Удалить"),role:.destructive) {
     if let value=deletingServer { model.mutate(["action":"delete-node","revision":model.configuration.revision,"id":value.id]) };deletingServer=nil
    }
   } message:{Text(deletingServer?.name ?? "")}
   .alert(L("Удалить правило?"),isPresented:Binding(get:{deletingGroup != nil},set:{if !$0 { deletingGroup=nil }})) {
    Button(L("Отменить"),role:.cancel) { deletingGroup=nil }
    Button(L("Удалить"),role:.destructive) {
     if let value=deletingGroup { model.mutate(["action":"delete-group","revision":model.configuration.revision,"id":value.id]) };deletingGroup=nil
    }
   } message:{Text(deletingGroup?.title ?? "")}
 }
}

private struct ProfileImportSheet:View {
 @ObservedObject var model:Center
 var body:some View {
  if let draft=model.profileImport {
   VStack(alignment:.leading,spacing:14) {
    Text(L("Импортировать настройки?")).font(.headline)
    Text(L("Текущие настройки будут заменены.")).font(.callout)
    Grid(alignment:.leading,horizontalSpacing:20,verticalSpacing:6) {
     GridRow { Text(L("Серверы")).foregroundStyle(.secondary);Text("\(draft.preview.servers)") }
     GridRow { Text(L("Правила")).foregroundStyle(.secondary);Text("\(draft.preview.groups)") }
     GridRow { Text(L("Наборы правил")).foregroundStyle(.secondary);Text("\(draft.preview.snapshots)") }
    }.font(.callout)
    Text(draft.preview.killSwitch ? L("Есть маршруты с kill switch."):L("Kill switch выключен.")).font(.callout).foregroundStyle(.secondary)
    if draft.preview.bypass == true { Text(L("Байпас включён: трафик пойдёт напрямую, защита будет приостановлена.")).font(.callout).foregroundStyle(.orange) }
    if let error=draft.error { Text(error).font(.callout).foregroundStyle(.red).fixedSize(horizontal:false,vertical:true) }
    HStack {
     Button(L("Отменить")) { model.profileImport=nil }.keyboardShortcut(.cancelAction).modifier(EditorActionStyle())
     Spacer()
     Button { model.applyProfile() } label:{
      HStack { if draft.saving { ProgressView().controlSize(.mini) };Text(L("Заменить")) }
     }.keyboardShortcut(.defaultAction).modifier(EditorActionStyle(prominent:true))
    }.disabled(draft.saving)
   }.padding(20).frame(width:350).interactiveDismissDisabled(draft.saving)
  }
 }
}
