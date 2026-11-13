import SwiftUI
import AppKit

struct SettingsRoot:View {
 @ObservedObject var model:Center
 var body:some View {
  NavigationSplitView {
   List(model.settingsPanes,selection:$model.settingsPane) { pane in
    Label(pane.title,systemImage:pane.symbol).tag(pane)
   }.listStyle(.sidebar).toolbar(removing:.sidebarToggle).navigationSplitViewColumnWidth(min:210,ideal:215,max:260)
  } detail:{
   VStack(spacing:0) {
    Group {
     switch model.settingsPane ?? .routes {
     case .routes:RoutesPane(model:model)
     case .servers:ServersPane(model:model)
     case .tailnet:TailnetPane(model:model)
     case .remote:RemotePane(model:model)
     case .general:GeneralPane(model:model)
     }
    }.frame(maxWidth:.infinity,maxHeight:.infinity)
    if let error=model.error {
     Divider()
     HStack(alignment:.top,spacing:6) {
      Image(systemName:"exclamationmark.triangle.fill").foregroundStyle(.red).accessibilityHidden(true)
      Text(error).font(.callout).fixedSize(horizontal:false,vertical:true).textSelection(.enabled)
      Spacer(minLength:0)
     }.padding(10)
    }
   }.navigationTitle((model.settingsPane ?? .routes).title)
  }.onChange(of:model.settingsPane) { _,value in
   if !model.preview,let value { UserDefaults.standard.set(value.rawValue,forKey:"settingsPane") }
  }
  .sheet(isPresented:Binding(get:{model.profileImport != nil},set:{if !$0 && model.profileImport?.saving != true { model.profileImport=nil }})) {
   ProfileImportSheet(model:model)
  }
 }
}
struct ListActions<Extra:View>:View {
 let addTitle:String
 let canDelete:Bool
 let add:()->Void
 let delete:()->Void
 @ViewBuilder var extra:Extra
 var body:some View {
  HStack(spacing:8) {
   ControlGroup {
    Button(action:add) { Image(systemName:"plus") }.keyboardShortcut("n",modifiers:.command).help(addTitle).accessibilityLabel(addTitle)
    Button(role:.destructive,action:delete) { Image(systemName:"minus") }.help(L("Удалить выбранное")).accessibilityLabel(L("Удалить выбранное")).disabled(!canDelete)
   }.fixedSize()
   extra
   Spacer()
  }.controlSize(.small).padding(8)
 }
}
private let newItem="+new"
struct RoutesPane:View {
 @ObservedObject var model:Center
 @State private var selection:String?
 @State private var editing:RouteGroup?
 @State private var revision=0
 @State private var token=UUID()
 @State private var dirty=false
 @State private var pending:String??
 @State private var deleting:RouteGroup?
 var selected:RouteGroup? { model.configuration.routes.first{$0.id==selection} }
 func open(_ value:String?) {
  dirty=false;pending=nil
  if value == newItem { selection=nil;editing=RouteGroup.empty();revision=model.configuration.revision;token=UUID();return }
  selection=value
  guard let value else { editing=nil;return }
  model.loadEditor { ok in
   guard ok,selection == value else { return }
   editing=model.configuration.routes.first{$0.id==value};revision=model.configuration.revision;token=UUID()
  }
 }
 func request(_ value:String?) {
  guard value != selection || value == newItem else { return }
  if dirty { pending = .some(value) } else { open(value) }
 }
 func move(_ value:RouteGroup,by offset:Int) {
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
 var list:some View {
  VStack(spacing:0) {
   List(selection:Binding(get:{selection},set:{request($0)})) {
    Section {
     ForEach(model.configuration.groups) { item in row(item) }.onMove(perform:moveGroups)
    }
    Section { row(model.configuration.fallback) }
   }.listStyle(.inset).onDeleteCommand { if selection != "fallback" { deleting=selected } }
   if model.configuration.groups.count>1 {
    Text(L("Срабатывает первое совпадение сверху вниз")).font(.caption).foregroundStyle(.secondary).frame(maxWidth:.infinity,alignment:.leading).padding(.horizontal,12).padding(.vertical,6)
   }
   Divider()
   ListActions(addTitle:L("Добавить маршрут"),canDelete:selected != nil && selection != "fallback",add:{request(newItem)},delete:{deleting=selected}) {
    if let selected,selected.id != "fallback" {
     ControlGroup {
      Button { move(selected,by:-1) } label:{Image(systemName:"chevron.up")}.help(L("Выше")).accessibilityLabel(L("Выше")).disabled(selection==model.configuration.groups.first?.id)
      Button { move(selected,by:1) } label:{Image(systemName:"chevron.down")}.help(L("Ниже")).accessibilityLabel(L("Ниже")).disabled(selection==model.configuration.groups.last?.id)
     }.fixedSize()
    }
   }
  }
 }
 func row(_ item:RouteGroup)->some View {
  HStack(spacing:8) {
   Text(item.flag.isEmpty ? "↗":item.flag).frame(width:20)
   VStack(alignment:.leading,spacing:1) {
    Text(item.displayTitle).lineLimit(1)
    Text(!item.enabled ? L("Выключен"):item.throughTailnet ? "Tailnet"+(item.overrideAddress.flatMap{$0.isEmpty ? nil:" → "+$0} ?? ""):chainSymbols(item.nodes,servers:model.configuration.nodes)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
   }
   Spacer(minLength:4)
   if item.protected { Image(systemName:"hand.raised.fill").font(.system(size:10)).foregroundStyle(.red).help(L("Kill switch включён")).accessibilityLabel(L("Kill switch включён")) }
  }.tag(item.id).contentShape(Rectangle())
   .contextMenu {
    if item.id != "fallback" {
     Button(L("Выше")) { move(item,by:-1) }.disabled(item.id==model.configuration.groups.first?.id)
     Button(L("Ниже")) { move(item,by:1) }.disabled(item.id==model.configuration.groups.last?.id)
     Divider()
     Button(L("Удалить…"),role:.destructive) { deleting=item }
    }
   }
 }
 var body:some View {
  HStack(spacing:0) {
   list.frame(width:250)
   Divider()
   Group {
    if let editing {
     GroupEditor(model:model,group:editing,revision:revision,dirty:$dirty,cancel:{ if selection == nil { self.editing=nil };dirty=false;token=UUID() },saved:{ id in open(id) },delete:editing.id == "fallback" || selection == nil ? nil:{ deleting=editing }).id(token)
    } else {
     ContentUnavailableView(L("Выберите маршрут"),systemImage:"arrow.triangle.branch",description:Text(L("Маршрут отправляет выбранные домены и адреса через цепочку серверов.")))
    }
   }.frame(minWidth:0,maxWidth:.infinity,maxHeight:.infinity).clipped()
  }.disabled(model.busy != nil && model.busy != "save-group:")
   .onAppear { if let item=model.settingsItem { model.settingsItem=nil;open(item) } }
   .onChange(of:model.settingsItem) { _,item in if let item { model.settingsItem=nil;request(item) } }
   .alert(L("Удалить маршрут?"),isPresented:Binding(get:{deleting != nil},set:{if !$0 { deleting=nil }})) {
    Button(L("Отменить"),role:.cancel) { deleting=nil }
    Button(L("Удалить"),role:.destructive) {
     if let value=deleting { model.mutate(["action":"delete-group","revision":model.configuration.revision,"id":value.id]) { reply in if reply.ok { open(nil) } } };deleting=nil
    }
   } message:{Text(deleting?.title ?? "")}
   .alert(L("Есть несохранённые изменения"),isPresented:Binding(get:{pending != nil},set:{if !$0 { pending=nil }})) {
    Button(L("Продолжить редактирование"),role:.cancel) { pending=nil }
    Button(L("Отбросить"),role:.destructive) { if let value=pending { open(value) } }
   }
 }
}
struct ServersPane:View {
 @ObservedObject var model:Center
 @State private var selection:String?
 @State private var editing:ServerDraft?
 @State private var revision=0
 @State private var token=UUID()
 @State private var dirty=false
 @State private var pending:String??
 @State private var deleting:Server?
 var selected:Server? { model.configuration.nodes.first{$0.id==selection} }
 func open(_ value:String?) {
  dirty=false;pending=nil
  if value == newItem { selection=nil;editing=ServerDraft();revision=model.configuration.revision;token=UUID();return }
  selection=value
  guard let value else { editing=nil;return }
  model.loadEditor { ok in
   guard ok,selection == value else { return }
   editing=model.configuration.nodes.first{$0.id==value}.map{ServerDraft($0)};revision=model.configuration.revision;token=UUID()
  }
 }
 func request(_ value:String?) {
  guard value != selection || value == newItem else { return }
  if dirty { pending = .some(value) } else { open(value) }
 }
 var list:some View {
  VStack(spacing:0) {
   Group {
    if model.configuration.nodes.isEmpty {
     ContentUnavailableView(L("Нет серверов"),systemImage:"server.rack",description:Text(L("Добавьте VLESS-сервер вручную или по ссылке.")))
    } else {
     List(selection:Binding(get:{selection},set:{request($0)})) {
      ForEach(model.configuration.nodes) { node in
       let path=model.paths.first{$0.id==node.id}
       HStack(spacing:8) {
        Text(node.symbol).frame(width:20)
        Text(node.name).lineLimit(1).help(node.name)
        Spacer(minLength:6)
        if let path { Text(path.latency.map{String(Int($0))+L(" мс")} ?? L("нет ответа")).font(.caption).monospacedDigit().foregroundStyle(path.success ? Color.secondary:Color.orange).help(L("{0}: доступность узла по ICMP, не всей VPN-цепочки", path.name)) }
       }.tag(node.id).contentShape(Rectangle())
        .contextMenu { Button(L("Удалить…"),role:.destructive) { deleting=node } }
      }
     }.listStyle(.inset).onDeleteCommand { deleting=selected }
    }
   }.frame(maxHeight:.infinity)
   Divider()
   ListActions(addTitle:L("Добавить сервер"),canDelete:selected != nil,add:{request(newItem)},delete:{deleting=selected}) { EmptyView() }
  }
 }
 var body:some View {
  HStack(spacing:0) {
   list.frame(width:250)
   Divider()
   Group {
    if let editing {
     ServerEditor(model:model,session:EditorSession(original:editing,draft:editing,revision:revision),dirty:$dirty,cancel:{ if selection == nil { self.editing=nil };dirty=false;token=UUID() },saved:{ id in open(id) },delete:selection == nil ? nil:{ deleting=selected }).id(token)
    } else {
     ContentUnavailableView(L("Выберите сервер"),systemImage:"server.rack",description:Text(L("Серверы объединяются в цепочки маршрутов.")))
    }
   }.frame(minWidth:0,maxWidth:.infinity,maxHeight:.infinity).clipped()
  }.disabled(model.busy != nil && model.busy != "save-node:")
   .onAppear { if let item=model.settingsItem { model.settingsItem=nil;open(item) } }
   .alert(L("Удалить сервер?"),isPresented:Binding(get:{deleting != nil},set:{if !$0 { deleting=nil }})) {
    Button(L("Отменить"),role:.cancel) { deleting=nil }
    Button(L("Удалить"),role:.destructive) {
     if let value=deleting { model.mutate(["action":"delete-node","revision":model.configuration.revision,"id":value.id]) { reply in if reply.ok { open(nil) } } };deleting=nil
    }
   } message:{Text(deleting?.name ?? "")}
   .alert(L("Есть несохранённые изменения"),isPresented:Binding(get:{pending != nil},set:{if !$0 { pending=nil }})) {
    Button(L("Продолжить редактирование"),role:.cancel) { pending=nil }
    Button(L("Отбросить"),role:.destructive) { if let value=pending { open(value) } }
   }
 }
}
struct GeneralPane:View {
 @ObservedObject var model:Center
 @Environment(\.colorScheme) private var colorScheme
 @State private var confirmingBypass=false
 var ready:Bool { model.busy == nil && model.installed && !model.needsUpdate }
 var version:String { Bundle.main.object(forInfoDictionaryKey:"CFBundleShortVersionString") as? String ?? "" }
 var body:some View {
  Form {
   Section {
    Toggle(L("Запускать при входе"),isOn:Binding(get:{model.launchAtLogin},set:{_ in model.toggleLogin()}))
    Toggle(isOn:Binding(get:{model.configuration.bypassed},set:{ value in
     if value && model.configuration.routes.contains(where:\.protected) { confirmingBypass=true } else { model.toggleBypass() }
    })) {
     Text(L("Байпас"))
     Text(L("Весь трафик идёт напрямую, защита приостановлена. Настройки маршрутов сохраняются.")).font(.caption).foregroundStyle(.secondary)
    }.disabled(!ready)
   }
   Section(L("Настройки")) {
    LabeledContent(L("Перенос на другой Mac")) {
     HStack {
      Button(L("Импортировать…")) { model.importProfile() }
      Button(L("Экспортировать…")) { model.exportProfile() }
     }.disabled(!ready)
    }
   }
   Section(L("Обслуживание")) {
    LabeledContent(L("Сетевой движок")) { Button(L("Переподключить всё")) { model.mutate(["action":"reconnect"]) }.disabled(!ready) }
    LabeledContent {
     Button(L("Восстановить помощник…")) { model.privileged("install") }.disabled(model.busy != nil)
    } label:{
     Text(L("Системный помощник"))
     Text(L("Повторить установку помощника при неполадках, сохранив настройки. Нужен доступ администратора.")).font(.caption).foregroundStyle(.secondary)
    }
   }
   Section {
    HStack {
     Image(nsImage:BrandAssets.wordmark(dark:colorScheme == .dark)).resizable().scaledToFit().frame(width:48,height:20).accessibilityLabel("libvpn")
     Spacer()
     Text(L("Версия {0}",version)).foregroundStyle(.secondary)
    }
   }
  }.formStyle(.grouped)
   .alert(L("Включить байпас?"),isPresented:$confirmingBypass) {
    Button(L("Отменить"),role:.cancel) {}
    Button(L("Включить байпас"),role:.destructive) { model.toggleBypass() }
   } message:{ Text(L("Весь трафик пойдёт напрямую, kill switch перестанет защищать маршруты.")) }
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
     GridRow { Text(L("Маршруты")).foregroundStyle(.secondary);Text("\(draft.preview.groups)") }
     GridRow { Text(L("Наборы правил")).foregroundStyle(.secondary);Text("\(draft.preview.snapshots)") }
    }.font(.callout)
    Text(draft.preview.killSwitch ? L("Есть маршруты с kill switch."):L("Kill switch выключен.")).font(.callout).foregroundStyle(.secondary)
    if draft.preview.remoteAccess == true { Text(L("Профиль включает удалённый доступ и его ключи.")).font(.callout).foregroundStyle(.secondary) }
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
