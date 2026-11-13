import SwiftUI

struct TrafficGraph:View {
 let values:[Double]
 var body:some View {
  GeometryReader { geometry in
   Path { path in
    for (index,value) in values.enumerated() {
     let point=CGPoint(x:geometry.size.width*CGFloat(index)/CGFloat(max(values.count-1,1)),y:geometry.size.height*(1-CGFloat(min(1,log10(max(0,value)+1)/8))))
     if index==0 { path.move(to:point) } else { path.addLine(to:point) }
    }
   }.stroke(Color.accentColor,style:StrokeStyle(lineWidth:1.4,lineCap:.round,lineJoin:.round))
  }.accessibilityHidden(true)
 }
}
struct StatusPresentation {
 let title:String
 let detail:String
 let symbol:String
 let color:Color
 init(_ status:AppStatus) {
  switch status {
  case .needsHelper:title=L("Нужен системный помощник");detail=L("Установка требует доступа администратора");symbol="wrench.and.screwdriver.fill";color = .secondary
  case .updating:title=L("Обновление помощника");detail=L("Настройки сохранятся");symbol="arrow.down.circle.fill";color = .secondary
  case .failure:title=L("Движок остановлен");detail=L("Проверьте серверы и настройки");symbol="exclamationmark.triangle.fill";color = .red
  case .bypass:title=L("Байпас");detail=L("Весь трафик идёт напрямую, защита приостановлена");symbol="pause.circle.fill";color = .orange
  case .needsServer:title=L("Серверов пока нет");detail=L("Добавьте сервер, чтобы настроить маршруты");symbol="server.rack";color = .secondary
  case .starting:title=L("Подключение…");detail=L("Применяем маршруты");symbol="circle.dotted";color = .orange
  case .blocked:title=L("Трафик заблокирован");detail=L("Маршруты с kill switch выключены");symbol="hand.raised.fill";color = .red
  case .off:title=L("Маршруты выключены");detail=L("Весь трафик идёт напрямую");symbol="power.circle";color = .secondary
  case .active(let routes,let total):title=L("Маршрутизация включена");detail=L("Активно маршрутов: {0} из {1}",routes,total);symbol="checkmark.shield.fill";color = .green
  }
 }
}
struct RouteRow:View {
 let group:RouteGroup
 @ObservedObject var model:Center
 var isBusy:Bool { model.busy != nil }
 var bypassed:Bool { model.configuration.bypassed }
 var tailnetTitle:String {
  guard let address=group.overrideAddress,!address.isEmpty else { return "Tailnet" }
  return "Tailnet → "+address
 }
 var chainHelp:String {
  group.nodes.compactMap { id in model.configuration.nodes.first { $0.id==id }?.name }.joined(separator:" → ")
 }
 func chainNode(_ id:String)->some View {
  let path=model.paths.first { $0.id==id }
  let symbol=model.configuration.nodes.first { $0.id==id }?.symbol ?? "?"
  let latency:String
  if group.nodes.count<=3,let value=path?.latency { latency=" "+String(Int(value))+L(" мс") }
  else { latency="" }
  let color:Color=path?.success == false ? .orange:.secondary
  return Text(symbol+latency).foregroundStyle(color)
 }
 var chainSubtitle:some View {
  HStack(spacing:3) {
   ForEach(Array(group.nodes.enumerated()),id:\.element) { index,id in
    if index>0 { Text("›").foregroundStyle(.tertiary) }
    chainNode(id)
   }
  }.help(chainHelp)
 }
 @ViewBuilder var subtitle:some View {
  if bypassed { Text(L("Напрямую: включён байпас")).foregroundStyle(.secondary) }
  else if group.throughTailnet {
   if group.enabled && model.configuration.tailnet?.enabled == true { Text(tailnetTitle).foregroundStyle(.secondary) }
   else if group.protected { Text(L("Заблокирован kill switch")).foregroundStyle(.red) }
   else { Text(group.enabled ? L("Напрямую: tailnet выключен"):L("Напрямую")).foregroundStyle(.secondary) }
  }
  else if group.nodes.isEmpty { Text(group.protected ? L("Заблокирован, нет цепочки"):L("Напрямую, нет цепочки")).foregroundStyle(group.protected ? Color.red:Color.secondary) }
  else if !group.enabled {
   if group.protected { Text(L("Заблокирован kill switch")).foregroundStyle(.red) }
   else { Text(L("Напрямую")).foregroundStyle(.secondary) }
  } else {
   chainSubtitle
  }
 }
 var body:some View {
  HStack(spacing:8) {
   Text(group.flag.isEmpty ? "↗":group.flag).font(.system(size:14)).frame(width:20).accessibilityHidden(true)
   VStack(alignment:.leading,spacing:1) {
    Text(group.displayTitle).font(.system(size:13)).lineLimit(1).help(group.displayTitle)
    subtitle.font(.system(size:11)).lineLimit(1)
   }
   Spacer(minLength:4)
   Button { model.toggleProtection(group) } label:{
    if model.busy=="set-kill-switch:"+group.id { ProgressView().controlSize(.mini).frame(width:18,height:18) }
    else { Image(systemName:group.protected ? "hand.raised.fill":"hand.raised").font(.system(size:12)).frame(width:18,height:18).contentShape(Rectangle()) }
   }.buttonStyle(.plain).foregroundStyle(group.protected ? Color.red:Color.secondary)
    .help(group.protected ? L("Выключить kill switch: {0}", group.displayTitle):L("Включить kill switch: {0}. При аварии движка блокируется весь внешний выход", group.displayTitle))
    .accessibilityLabel(group.protected ? L("Выключить kill switch: {0}", group.displayTitle):L("Включить kill switch: {0}. При аварии движка блокируется весь внешний выход", group.displayTitle))
    .disabled(isBusy || bypassed)
   if model.busy=="set-enabled:"+group.id { ProgressView().controlSize(.small).frame(width:32,height:18) }
   else {
    Toggle(group.enabled ? L("Отключить {0}", group.displayTitle):L("Включить {0}", group.displayTitle),isOn:Binding(get:{group.enabled},set:{_ in model.toggle(group)}))
     .toggleStyle(.switch).controlSize(.mini).labelsHidden()
     .help(group.nodes.isEmpty && !group.throughTailnet ? L("Сначала добавьте сервер в цепочку"):group.enabled ? L("Отключить {0}", group.displayTitle):L("Включить {0}", group.displayTitle))
     .disabled(isBusy || bypassed || (!group.enabled && group.nodes.isEmpty && !group.throughTailnet))
   }
   Menu {
    Button(L("Переподключить")) { model.mutate(["action":"reconnect-route","revision":model.configuration.revision,"id":group.id]) }.disabled(!group.enabled || isBusy || bypassed)
    if !group.throughTailnet { Button(L("Настроить цепочку…")) { model.editChain(group) }.disabled(isBusy) }
    Divider()
    Button(L("Изменить маршрут…")) { model.open(.routes,item:group.id) }
   } label:{
    if model.busy=="reconnect-route:"+group.id || model.busy=="set-chain:"+group.id { ProgressView().controlSize(.mini) }
    else { Image(systemName:"ellipsis") }
   }.menuStyle(.borderlessButton).menuIndicator(.hidden).frame(width:20).help(L("Действия: {0}", group.displayTitle)).accessibilityLabel(L("Действия: {0}", group.displayTitle))
  }.frame(height:40).opacity(bypassed ? 0.6:1)
 }
}
struct RemoteRow:View {
 let remote:RemoteAccess
 @ObservedObject var model:Center
 var description:(String,Color) {
  if !remote.enabled { return (L("Выключен"),.secondary) }
  switch model.remoteState {
  case "connected":return (L("Связь с сервером есть"),.green)
  case "stopped":return (L("Движок остановлен"),.red)
  default:return (L("Ожидание сервера"),.orange)
  }
 }
 var body:some View {
  HStack(spacing:8) {
   Image(systemName:"iphone").font(.system(size:14)).foregroundStyle(.secondary).frame(width:20).accessibilityHidden(true)
   VStack(alignment:.leading,spacing:1) {
    Text(L("Удалённый доступ")).font(.system(size:13)).lineLimit(1)
    Text(description.0).font(.system(size:11)).foregroundStyle(description.1).lineLimit(1)
   }
   Spacer(minLength:4)
   if model.busy=="set-remote-access:" { ProgressView().controlSize(.small).frame(width:32,height:18) }
   else {
    Toggle(L("Удалённый доступ"),isOn:Binding(get:{remote.enabled},set:{_ in model.toggleRemote()}))
     .toggleStyle(.switch).controlSize(.mini).labelsHidden().disabled(model.busy != nil)
   }
   Button { model.open(.remote) } label:{ Image(systemName:"chevron.right").font(.system(size:10,weight:.semibold)).frame(width:20,height:18).contentShape(Rectangle()) }
    .buttonStyle(.plain).foregroundStyle(.secondary).help(L("Настройки удалённого доступа")).accessibilityLabel(L("Настройки удалённого доступа"))
  }.frame(height:40)
 }
}
struct TailnetRow:View {
 let tailnet:Tailnet
 @ObservedObject var model:Center
 var description:(String,Color) {
  if !tailnet.enabled { return (L("Выключен"),.secondary) }
  switch model.tailnetState {
  case "connected":return (tailnet.hostname.isEmpty ? L("Подключено"):L("Подключено как {0}",tailnet.hostname),.green)
  case "stopped":return (L("Движок остановлен"),.red)
  case "login":return (L("Нужен ключ авторизации"),.orange)
  default:return (L("Подключение…"),.orange)
  }
 }
 var body:some View {
  HStack(spacing:8) {
   Image(systemName:"point.3.connected.trianglepath.dotted").font(.system(size:13)).foregroundStyle(.secondary).frame(width:20).accessibilityHidden(true)
   VStack(alignment:.leading,spacing:1) {
    Text("Tailnet").font(.system(size:13)).lineLimit(1)
    Text(description.0).font(.system(size:11)).foregroundStyle(description.1).lineLimit(1)
   }
   Spacer(minLength:4)
   if model.busy=="set-tailnet:" { ProgressView().controlSize(.small).frame(width:32,height:18) }
   else {
    Toggle("Tailnet",isOn:Binding(get:{tailnet.enabled},set:{_ in model.toggleTailnet()}))
     .toggleStyle(.switch).controlSize(.mini).labelsHidden().disabled(model.busy != nil)
   }
   Button { model.open(.tailnet) } label:{ Image(systemName:"chevron.right").font(.system(size:10,weight:.semibold)).frame(width:20,height:18).contentShape(Rectangle()) }
    .buttonStyle(.plain).foregroundStyle(.secondary).help(L("Настройки tailnet")).accessibilityLabel(L("Настройки tailnet"))
  }.frame(height:40)
 }
}
struct CompactChainEditor:View {
 @ObservedObject var model:Center
 @State var group:RouteGroup
 let revision:Int
 @State var error:String?
 var body:some View {
  VStack(alignment:.leading,spacing:10) {
   Text(group.flag+" "+group.displayTitle).font(.headline)
   EditorChain(servers:model.configuration.nodes,nodes:$group.nodes).frame(height:CGFloat(min(max(group.nodes.count,3),8))*28+38)
   if let error { Text(error).font(.callout).foregroundStyle(.red).fixedSize(horizontal:false,vertical:true) }
   HStack {
    Spacer()
    Button(L("Отменить")) { model.editingChain=nil }.keyboardShortcut(.cancelAction)
    Button(L("Сохранить")) {
     model.mutate(["action":"set-chain","revision":revision,"id":group.id,"nodes":group.nodes]) { reply in
      if reply.ok { model.editingChain=nil } else { error=reply.error }
     }
    }.keyboardShortcut(.defaultAction).disabled(group.enabled && group.nodes.isEmpty)
   }.disabled(model.busy != nil)
  }.padding(14).frame(width:340)
 }
}
struct PopoverContent:View {
 @ObservedObject var model:Center
 @AppStorage("popoverServersExpanded") private var serversExpanded=false
 @State private var confirmingBypass=false
 var status:AppStatus { model.status }
 var managed:Bool { model.installed && !model.needsUpdate }
 var hasProtection:Bool { model.configuration.routes.contains(where:\.protected) }
 func toggleBypass() {
  if !model.configuration.bypassed && hasProtection { confirmingBypass=true } else { model.toggleBypass() }
 }
 var header:some View {
  let presentation=StatusPresentation(status)
  return HStack(spacing:10) {
   Image(systemName:presentation.symbol).font(.system(size:22)).foregroundStyle(presentation.color).frame(width:28).accessibilityHidden(true)
   VStack(alignment:.leading,spacing:1) {
    Text(presentation.title).font(.system(size:14,weight:.semibold)).lineLimit(1)
    Text(presentation.detail).font(.system(size:11)).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
   }
   Spacer(minLength:4)
   if managed && !model.configuration.nodes.isEmpty {
    Button(action:toggleBypass) {
     if model.busy=="set-bypass:" { ProgressView().controlSize(.mini).frame(width:16,height:16) }
     else { Image(systemName:model.configuration.bypassed ? "play.fill":"pause.fill").font(.system(size:11)).frame(width:16,height:16) }
    }.buttonStyle(.bordered).controlSize(.small).disabled(model.busy != nil)
     .help(model.configuration.bypassed ? L("Выключить байпас"):L("Байпас: весь трафик напрямую, защита приостановлена"))
     .accessibilityLabel(model.configuration.bypassed ? L("Выключить байпас"):L("Байпас"))
   }
  }
 }
 @ViewBuilder var setup:some View {
  if model.needsUpdate {
   HStack(spacing:8) {
    if model.busy == "install" { ProgressView().controlSize(.small) }
    Text(model.busy == "install" ? (model.authorizingHelper ? L("Подтвердите запрос macOS. Настройки сохранятся."):L("Обновление помощника…")):L("Обновление помощника не завершено"))
     .font(.system(size:12)).fixedSize(horizontal:false,vertical:true)
   }
   if model.busy != "install" { Button(L("Восстановить помощник…")) { model.privileged("install") }.disabled(model.busy != nil) }
  } else if !model.installed {
   Text(L("Без него маршруты не работают. Серверы и маршруты добавляются после установки.")).font(.system(size:12)).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
   Button(L("Установить помощник…")) { model.privileged("install") }.buttonStyle(.borderedProminent).disabled(model.busy != nil)
  } else if model.configuration.nodes.isEmpty {
   Button(L("Добавить сервер…")) { model.open(.servers) }.buttonStyle(.borderedProminent)
  }
 }
 var routes:some View {
  ScrollView {
   VStack(spacing:0) {
    ForEach(model.configuration.routes) { group in
     RouteRow(group:group,model:model)
    }
   }
  }.frame(height:min(CGFloat(model.configuration.routes.count),6)*40)
 }
 var traffic:some View {
  HStack(spacing:10) {
   Text("↓ \(formatRate(model.rxRate))   ↑ \(formatRate(model.txRate))").font(.system(size:11)).monospacedDigit().foregroundStyle(.secondary).lineLimit(1).fixedSize()
   TrafficGraph(values:model.history).frame(height:18)
  }.help(L("Весь трафик физических сетевых интерфейсов Mac"))
 }
 var servers:some View {
  DisclosureGroup(isExpanded:$serversExpanded) {
   ScrollView {
    VStack(spacing:4) {
     ForEach(model.paths) { path in
      HStack(spacing:8) {
       Text(model.configuration.nodes.first{$0.id==path.id}?.symbol ?? path.flag).frame(width:20)
       Text(path.name).font(.system(size:12)).lineLimit(1).truncationMode(.middle)
       Spacer(minLength:8)
       Text(path.latency.map{String(Int($0))+L(" мс")} ?? L("нет ответа")).font(.system(size:11)).monospacedDigit().foregroundStyle(path.success ? Color.secondary:Color.orange)
      }.help(L("{0}: доступность узла по ICMP, не всей VPN-цепочки", path.name))
     }
    }.padding(.top,4)
   }.frame(height:min(CGFloat(model.paths.count),5)*21)
  } label:{
   Text(L("Серверы: {0}",model.paths.count)).font(.system(size:12)).foregroundStyle(.secondary)
  }
 }
 var footer:some View {
  HStack(spacing:6) {
   Button { model.openSettings?() } label:{ Label(L("Настройки…"),systemImage:"gearshape") }
    .buttonStyle(.plain).font(.system(size:12)).foregroundStyle(.secondary).disabled(!managed).keyboardShortcut(",",modifiers:.command)
   if model.busy=="install" || model.busy=="reconnect:" || model.busy=="refresh-rule-sets:" { ProgressView().controlSize(.mini).accessibilityLabel(L("Применение изменений")) }
   Spacer()
   Menu {
    Button(L("Обновить")) { model.refresh();model.refreshMetrics() }
    Button(L("Обновить наборы правил")) { model.mutate(["action":"refresh-rule-sets"]) }.disabled(!managed || model.busy != nil)
     .help(L("Скачать новые версии наборов GeoIP, Geosite и наборов по ссылке. Если что-то изменилось, движок перезапустится."))
    Button(L("Переподключить всё")) { model.mutate(["action":"reconnect"]) }.disabled(!managed || model.busy != nil)
    if model.installed {
     Button(L("Восстановить помощник…")) { model.privileged("install") }.disabled(model.busy != nil)
      .help(L("Повторить установку помощника при неполадках, сохранив настройки. Нужен доступ администратора."))
    }
    Divider()
    Toggle(L("Запускать при входе"),isOn:Binding(get:{model.launchAtLogin},set:{_ in model.toggleLogin()}))
    Divider()
    Button(L("Завершить libvpn")) { NSApp.terminate(nil) }
   } label:{ Image(systemName:"ellipsis.circle").frame(width:24,height:20) }
    .menuStyle(.borderlessButton).menuIndicator(.hidden).frame(width:24).help(L("Меню libvpn")).accessibilityLabel(L("Меню libvpn"))
  }
 }
 var body:some View {
  VStack(alignment:.leading,spacing:10) {
   header
   if let error=model.error {
    HStack(alignment:.top,spacing:6) {
     Image(systemName:"exclamationmark.triangle.fill").foregroundStyle(.red).font(.system(size:11)).accessibilityHidden(true)
     Text(error).font(.system(size:11)).fixedSize(horizontal:false,vertical:true).textSelection(.enabled)
    }.padding(8).frame(maxWidth:.infinity,alignment:.leading).background(Color.red.opacity(0.12),in:RoundedRectangle(cornerRadius:8))
   }
   setup
   if managed && !model.configuration.nodes.isEmpty {
    Divider()
    routes
    if let tailnet=model.configuration.tailnet { Divider();TailnetRow(tailnet:tailnet,model:model) }
    if let remote=model.configuration.remoteAccess { Divider();RemoteRow(remote:remote,model:model) }
    Divider()
    traffic
    if !model.paths.isEmpty { servers }
   }
   Divider()
   footer
  }.popover(item:$model.editingChain,arrowEdge:.leading) { group in CompactChainEditor(model:model,group:group,revision:model.configuration.revision) }
   .alert(L("Включить байпас?"),isPresented:$confirmingBypass) {
    Button(L("Отменить"),role:.cancel) {}
    Button(L("Включить байпас"),role:.destructive) { model.toggleBypass() }
   } message:{ Text(L("Весь трафик пойдёт напрямую, kill switch перестанет защищать маршруты.")) }
   .padding(14).frame(width:360).fixedSize(horizontal:false,vertical:true)
 }
}
