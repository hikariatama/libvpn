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
   }.stroke(Color.accentColor,lineWidth:1.4)
  }.accessibilityHidden(true)
 }
}
struct RouteRow:View {
 let group:RouteGroup
 @ObservedObject var model:Center
 var statusColor:Color {
  if model.configuration.bypassed { return .orange }
  if !group.enabled { return group.protected ? .red:.secondary }
  return model.state=="on" ? .green:model.state=="error" ? .red:.orange
 }
 @ViewBuilder func control(_ symbol:String,action:String,active:Bool=false,color:Color = .accentColor,help:String,perform:@escaping()->Void)->some View {
  Button(action:perform) {
   if model.busy==action+":"+group.id { ProgressView().controlSize(.mini).frame(width:23,height:23) }
   else { Image(systemName:symbol).font(.system(size:10,weight:.medium)).frame(width:23,height:23).contentShape(Circle()) }
  }.buttonStyle(.plain).foregroundStyle(active ? Color.white:Color.primary)
   .background(active ? color:Color.primary.opacity(0.08),in:Circle()).help(help).accessibilityLabel(help)
   .disabled(model.busy != nil || model.configuration.bypassed)
 }
 var body:some View {
  HStack(spacing:6) {
   HStack(spacing:6) {
    Text(group.flag).frame(width:16).accessibilityHidden(true)
    Text(group.displayTitle).font(.system(size:11,weight:.semibold)).lineLimit(1)
   }.frame(width:102,alignment:.leading).help(group.displayTitle)
   Text(chainSymbols(group.nodes,servers:model.configuration.nodes)).font(.system(size:11)).lineLimit(1)
    .help(group.nodes.compactMap{id in model.configuration.nodes.first{$0.id==id}?.name}.joined(separator:" → "))
   Spacer(minLength:2)
   Circle().fill(statusColor).frame(width:7,height:7)
    .help(model.configuration.bypassed ? L("Байпас: прямой выход"):!group.enabled && group.protected ? L("Трафик маршрута заблокирован"):group.enabled ? L("Цепочка включена"):L("Прямой выход"))
   control("power",action:"set-enabled",active:group.enabled,help:group.enabled ? L("Отключить {0}", group.displayTitle):L("Включить {0}", group.displayTitle)) { model.toggle(group) }
    .disabled(!group.enabled && group.nodes.isEmpty)
   control("hand.raised.fill",action:"set-kill-switch",active:group.protected,color:.red,help:group.protected ? L("Выключить kill switch: {0}", group.displayTitle):L("Включить kill switch: {0}. При аварии движка блокируется весь внешний выход", group.displayTitle)) { model.toggleProtection(group) }
   control("arrow.triangle.2.circlepath",action:"reconnect-route",help:L("Переподключить {0}", group.displayTitle)) { model.mutate(["action":"reconnect-route","revision":model.configuration.revision,"id":group.id]) }.disabled(!group.enabled)
   control("slider.horizontal.3",action:"set-chain",help:L("Настроить цепочку: {0}", group.displayTitle)) { model.editChain(group) }
  }.frame(height:30)
 }
}
struct CompactChainEditor:View {
 @ObservedObject var model:Center
 @State var group:RouteGroup
 let revision:Int
 @State var error:String?
 @State var dragging:String?
 var body:some View {
  VStack(alignment:.leading,spacing:10) {
   HStack {
    Text(group.flag+" "+group.displayTitle).font(.system(size:12,weight:.semibold));Spacer()
    Menu {
     ForEach(model.configuration.nodes.filter{!group.nodes.contains($0.id)}) { node in
      Button(node.symbol+" "+node.name) { group.nodes.append(node.id) }
     }
    } label:{Image(systemName:"plus.circle").frame(width:23,height:23)}
     .menuStyle(.borderlessButton).menuIndicator(.hidden).frame(width:23).help(L("Добавить сервер"))
   }
   Divider()
   if group.nodes.isEmpty { Text(L("Добавьте сервер в цепочку")).font(.caption).foregroundStyle(.secondary).padding(.vertical,10) }
   ScrollView {
    VStack(spacing:3) {
     ForEach(Array(group.nodes.enumerated()),id:\.element) { index,id in
      let node=model.configuration.nodes.first{$0.id==id}
      HStack(spacing:6) {
       Image(systemName:"line.3.horizontal").foregroundStyle(.secondary)
       Text(node?.symbol ?? "?");Text(node?.name ?? id).lineLimit(1).truncationMode(.middle).help(node?.name ?? id)
       Spacer()
       Button { if index>0 { group.nodes.swapAt(index,index-1) } } label:{Image(systemName:"chevron.up")}.disabled(index==0).accessibilityLabel(L("Переместить выше"))
       Button { if index+1<group.nodes.count { group.nodes.swapAt(index,index+1) } } label:{Image(systemName:"chevron.down")}.disabled(index+1==group.nodes.count).accessibilityLabel(L("Переместить ниже"))
       Button { group.nodes.removeAll{$0==id} } label:{Image(systemName:"minus.circle")}.accessibilityLabel(L("Удалить из цепочки"))
      }.font(.system(size:11)).buttonStyle(.plain).frame(height:28).contentShape(Rectangle())
       .onDrag { dragging=id;return NSItemProvider(object:id as NSString) }
       .onDrop(of:["public.utf8-plain-text"],delegate:ChainDropDelegate(target:id,dragging:$dragging,nodes:$group.nodes))
     }
    }
   }.frame(height:CGFloat(min(group.nodes.count,12))*31)
   if let error { Text(error).font(.caption).foregroundStyle(.red) }
   Divider()
   HStack {
    Button(L("Отменить")) { model.editingChain=nil }.keyboardShortcut(.cancelAction)
    Spacer()
    Button(L("Сохранить")) {
     model.mutate(["action":"set-chain","revision":revision,"id":group.id,"nodes":group.nodes]) { reply in
      if reply.ok { model.editingChain=nil } else { error=reply.error }
     }
    }.keyboardShortcut(.defaultAction).disabled(group.enabled && group.nodes.isEmpty)
   }.disabled(model.busy != nil)
  }.padding(12).frame(width:390)
 }
}
struct PopoverContent:View {
 @Environment(\.colorScheme) private var colorScheme
 @ObservedObject var model:Center
 var body:some View {
  VStack(alignment:.leading,spacing:8) {
   if model.needsUpdate {
    Text(model.busy == "install" ? L("Обновление помощника…"):L("Обновление помощника не завершено")).font(.system(size:12))
    if model.busy == "install" {
     Text(model.authorizingHelper ? L("Подтвердите запрос macOS. Настройки сохранятся."):L("Настройки сохранятся.")).font(.system(size:11)).foregroundStyle(.secondary)
    } else if model.error == nil {
     Text(L("Повторить можно через меню → Восстановить помощник…")).font(.system(size:11)).foregroundStyle(.secondary)
    }
   } else if !model.installed {
    Text(L("Для маршрутизации нужен системный помощник")).font(.system(size:12))
    Text(L("Установка требует доступа администратора. Серверы и правила добавляются в настройках.")).font(.system(size:11)).foregroundStyle(.secondary)
    Button(L("Установить помощник…")) { model.privileged("install") }.disabled(model.busy != nil)
   } else {
    if model.configuration.nodes.isEmpty {
     Button(L("Добавить сервер…")) { model.openSettings?() }.font(.system(size:12))
    }
    ScrollView {
     VStack(spacing:2) {
      ForEach(model.configuration.groups+[model.configuration.fallback]) { RouteRow(group:$0,model:model) }
     }
    }.frame(height:CGFloat(min(model.configuration.groups.count+1,8))*32-2)
    Divider()
    HStack(spacing:8) {
     Text("↓ \(formatRate(model.rxRate))  ↑ \(formatRate(model.txRate))").font(.system(size:10)).monospacedDigit()
     Spacer()
     TrafficGraph(values:model.history).frame(width:90,height:22)
    }.help(L("Весь трафик физических сетевых интерфейсов Mac"))
    if !model.paths.isEmpty {
     ScrollView {
      LazyVGrid(columns:[GridItem(.adaptive(minimum:62),alignment:.leading)],alignment:.leading,spacing:6) {
       ForEach(model.paths) { path in
        Text("\(model.configuration.nodes.first{$0.id==path.id}?.symbol ?? path.flag) \(path.latency.map{String(Int($0))+L(" мс")} ?? "—")")
         .font(.system(size:9)).foregroundStyle(path.success ? Color.secondary:Color.orange)
         .help(L("{0}: доступность узла по ICMP, не всей VPN-цепочки", path.name))
       }
      }
     }.frame(height:CGFloat(min((model.paths.count+5)/6,4))*18)
    }
   }
   if let error=model.error { Text(error).font(.system(size:10)).foregroundStyle(.red).fixedSize(horizontal:false,vertical:true) }
   HStack {
    Image(nsImage:BrandAssets.wordmark(dark:colorScheme == .dark)).resizable().scaledToFit()
     .frame(width:36,height:18).accessibilityLabel("libvpn")
    if model.busy=="install" || model.busy=="reconnect:" { ProgressView().controlSize(.mini).accessibilityLabel(L("Применение изменений")) }
    Spacer()
    Menu {
     Button(L("Настройки…")) { model.openSettings?() }.disabled(!model.installed || model.needsUpdate)
     Button(model.configuration.bypassed ? L("Выключить байпас"):L("Байпас")) { model.toggleBypass() }.disabled(model.busy != nil || !model.installed || model.needsUpdate)
     Button(L("Обновить")) { model.refresh();model.refreshMetrics() }
     Button(L("Переподключить")) { model.mutate(["action":"reconnect"]) }.disabled(!model.installed || model.needsUpdate || model.busy != nil)
     if model.installed {
      Button(L("Восстановить помощник…")) { model.privileged("install") }.disabled(model.busy != nil)
       .help(L("Повторить установку помощника при неполадках, сохранив настройки. Нужен доступ администратора."))
     }
     Divider()
     Button(model.launchAtLogin ? L("✓ Запускать при входе"):L("Запускать при входе"),action:model.toggleLogin)
     Divider()
     Button(L("Завершить libvpn")) { NSApp.terminate(nil) }
    } label: { Image(systemName:"ellipsis").frame(width:24,height:20) }
     .menuStyle(.borderlessButton).menuIndicator(.hidden).frame(width:24).help(L("Меню libvpn"))
   }
  }.popover(item:$model.editingChain,arrowEdge:.leading) { group in CompactChainEditor(model:model,group:group,revision:model.configuration.revision) }
   .padding(12).frame(width:450).fixedSize(horizontal:false,vertical:true)
 }
}
