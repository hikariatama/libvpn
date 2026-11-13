import SwiftUI

struct ServerEditor:View {
 @ObservedObject var model:Center
 @State var session:EditorSession<ServerDraft>
 @Binding var dirty:Bool
 let cancel:()->Void
 let saved:(String)->Void
 let delete:(()->Void)?
 @State private var useLink=false
 var isNew:Bool { !session.draft.hasUUID }
 func save() {
  session.saving=true;session.error=nil
  let payload:[String:Any]=useLink ? ["uri":session.draft.uri]:session.draft.payload
  let before=Set(model.configuration.nodes.map(\.id))
  model.mutate(["action":"save-node","revision":session.revision,"node":payload]) { reply in
   if session.finish(ok:reply.ok,error:reply.error) {
    saved(useLink ? (model.configuration.nodes.first{!before.contains($0.id)}?.id ?? session.draft.id):session.draft.id)
   }
  }
 }
 var body:some View {
  EditorPane(saving:session.saving,changed:session.changed,error:session.error,cancel:cancel,save:save,delete:delete,deleteTitle:L("Удалить сервер…")) {
   Form {
    if isNew {
     Section {
      Picker(L("Добавить"),selection:$useLink) { Text(L("Вручную")).tag(false);Text(L("Из ссылки VLESS")).tag(true) }.pickerStyle(.segmented)
       .onChange(of:useLink) { _,value in if !value { session.draft.uri="" } }
     }
    }
    if useLink {
     Section {
      SecureField(L("Ссылка"),text:$session.draft.uri,prompt:Text("vless://…"))
     } footer:{ Text(L("Поддерживаются ссылки VLESS с TCP и TLS.")).font(.caption).foregroundStyle(.secondary) }
    } else {
     Section {
      TextField(L("Название"),text:$session.draft.name)
      CountryPicker(selection:$session.draft.country)
     }
     Section(L("Подключение")) {
      TextField(L("Адрес"),text:$session.draft.address,prompt:Text("example.com"))
      TextField(L("Порт"),text:$session.draft.port)
      SecureField("UUID",text:$session.draft.uuid,prompt:Text(session.draft.hasUUID ? L("Не изменять"):L("UUID сервера")))
     }
     Section("TLS") {
      TextField(L("Имя сервера"),text:$session.draft.serverName,prompt:Text(session.draft.address))
      SecureField("Pin SHA-256",text:$session.draft.pin,prompt:Text(session.draft.hasPin ? L("Не изменять"):L("Системные CA"))).disabled(session.draft.clearPin)
      if session.draft.hasPin {
       Toggle(L("Удалить сохранённый pin"),isOn:$session.draft.clearPin)
        .onChange(of:session.draft.clearPin) { _,value in if value { session.draft.pin="" } }
      }
      Picker(L("Минимальная версия TLS"),selection:$session.draft.minTLS) {
       Text(L("Авто")).tag("");Text("1.2").tag("1.2");Text("1.3").tag("1.3")
      }
      Picker(L("Максимальная версия TLS"),selection:$session.draft.maxTLS) {
       Text(L("Авто")).tag("");Text("1.2").tag("1.2");Text("1.3").tag("1.3")
      }
     }
    }
   }.formStyle(.grouped)
  }.onChange(of:session.changed) { _,value in dirty=value }
 }
}

struct GroupEditor:View {
 @ObservedObject var model:Center
 @State var session:EditorSession<RouteGroup>
 @State private var domains:String
 @State private var suffixes:String
 @State private var keywords:String
 @State private var regexes:String
 @State private var cidrs:String
 @State private var matcher="suffixes"
 @Binding var dirty:Bool
 let cancel:()->Void
 let saved:(String)->Void
 let delete:(()->Void)?
 private let originalLines:[String]
 init(model:Center,group:RouteGroup,revision:Int,dirty:Binding<Bool>,cancel:@escaping()->Void,saved:@escaping(String)->Void,delete:(()->Void)?) {
  self.model=model;_dirty=dirty;self.cancel=cancel;self.saved=saved;self.delete=delete
  _session=State(initialValue:EditorSession(original:group,draft:group,revision:revision))
  let values=[group.domains ?? [],group.suffixes ?? [],group.keywords ?? [],group.regexes ?? [],group.cidrs ?? []].map{$0.joined(separator:"\n")}
  _domains=State(initialValue:values[0]);_suffixes=State(initialValue:values[1]);_keywords=State(initialValue:values[2])
  _regexes=State(initialValue:values[3]);_cidrs=State(initialValue:values[4])
  originalLines=values
 }
 var changed:Bool { session.changed || [domains,suffixes,keywords,regexes,cidrs] != originalLines }
 var rulesBinding:Binding<String> {
  switch matcher { case "suffixes":return $suffixes;case "keywords":return $keywords;case "regexes":return $regexes;case "cidrs":return $cidrs;default:return $domains }
 }
 var matcherTitle:String {
  switch matcher { case "suffixes":return L("Домены и поддомены");case "keywords":return L("Ключевые слова");case "regexes":return L("Регулярные выражения");case "cidrs":return "IP / CIDR";default:return L("Точные домены") }
 }
 func expand(_ contents:RuleSetContents) {
  domains=mergedLines(domains,contents.domains);suffixes=mergedLines(suffixes,contents.suffixes)
  keywords=mergedLines(keywords,contents.keywords);regexes=mergedLines(regexes,contents.regexes)
  cidrs=mergedLines(cidrs,contents.cidrs)
 }
 func save() {
  if session.draft.id != "fallback" {
   session.draft.domains=lines(domains);session.draft.suffixes=lines(suffixes);session.draft.cidrs=lines(cidrs)
   session.draft.keywords=lines(keywords);session.draft.regexes=lines(regexes)
  }
  session.saving=true;session.error=nil
  model.mutate(["action":"save-group","revision":session.revision,"group":session.draft.payload]) { reply in
   if session.finish(ok:reply.ok,error:reply.error) { saved(session.draft.id) }
  }
 }
 var body:some View {
  EditorPane(saving:session.saving,changed:changed,error:session.error,cancel:cancel,save:save,delete:delete,deleteTitle:L("Удалить маршрут…")) {
   Form {
    Section {
     LabeledContent(L("Название")) {
      HStack(spacing:8) {
       TextField("",text:$session.draft.flag,prompt:Text("↗")).labelsHidden().textFieldStyle(.roundedBorder)
        .multilineTextAlignment(.center).frame(width:38).help(L("Значок маршрута")).accessibilityLabel(L("Значок маршрута"))
       TextField(L("Название"),text:$session.draft.title).labelsHidden().textFieldStyle(.roundedBorder).multilineTextAlignment(.leading).frame(minWidth:0,maxWidth:.infinity).accessibilityLabel(L("Название маршрута"))
      }
     }
     if session.draft.id != "fallback" {
      Picker(L("Выход"),selection:Binding(get:{session.draft.exit ?? "chain"},set:{session.draft.exit = $0=="tailnet" ? "tailnet":nil})) {
       Text(L("Цепочка серверов")).tag("chain")
       Text("Tailnet").tag("tailnet").disabled(model.configuration.tailnet == nil && !session.original.throughTailnet)
      }.help(model.configuration.tailnet == nil ? L("Чтобы выбрать tailnet, сначала настройте его в разделе Tailnet."):L("Куда отправлять подходящий трафик."))
      TextField(L("Адрес назначения"),text:Binding(get:{session.draft.overrideAddress ?? ""},set:{session.draft.overrideAddress=$0.trimmingCharacters(in:.whitespaces)}),prompt:Text(L("Не менять")))
       .help(L("Соединения пойдут на этот IP, а имя сайта в запросе сохранится. Например, адрес сервера в tailnet."))
     }
     Toggle(session.draft.throughTailnet ? L("Включить маршрут"):L("Включить цепочку"),isOn:$session.draft.enabled)
     Toggle(isOn:Binding(get:{session.draft.protected},set:{session.draft.killSwitch=$0})) {
      Text("Kill switch")
      Text(session.draft.throughTailnet ? L("Пока маршрут или tailnet выключены, его трафик блокируется. При аварии движка закрывается весь внешний выход."):L("Отключённая цепочка блокирует трафик маршрута. При аварии движка закрывается весь внешний выход.")).font(.caption).foregroundStyle(.secondary)
     }
    }
    if !session.draft.throughTailnet {
    Section {
     EditorChain(servers:model.configuration.nodes,nodes:$session.draft.nodes)
      .frame(minWidth:0,maxWidth:.infinity).frame(height:CGFloat(min(max(session.draft.nodes.count,2),5))*28+38)
    } header:{ Text(L("Цепочка")) } footer:{ Text(L("Первым подключается верхний сервер, выход в интернет — через нижний.")).font(.caption).foregroundStyle(.secondary) }
    }
    if session.draft.id != "fallback" {
     Section {
      Picker(L("Тип условия"),selection:$matcher) {
       Text(L("Домены и поддомены")).tag("suffixes");Text(L("Точные домены")).tag("domains")
       Text(L("Ключевые слова")).tag("keywords");Text(L("Регулярные выражения")).tag("regexes");Text("IP / CIDR").tag("cidrs")
      }.pickerStyle(.menu)
      TextEditor(text:rulesBinding).font(.system(.body,design:.monospaced)).scrollContentBackground(.hidden)
       .frame(minWidth:0,maxWidth:.infinity,minHeight:110,maxHeight:110).accessibilityLabel(matcherTitle)
     } header:{ Text(L("Условия")) } footer:{ Text(L("Одно значение на строку. Любое совпадение выбирает этот маршрут.")).font(.caption).foregroundStyle(.secondary) }
     Section(L("Наборы правил")) {
      RuleSetSection(model:model,ruleSets:Binding(get:{session.draft.ruleSets ?? []},set:{session.draft.ruleSets=$0}),error:$session.error,expand:expand)
     }
    }
   }.formStyle(.grouped)
  }.onChange(of:changed) { _,value in dirty=value }
 }
}

struct RemotePane:View {
 @ObservedObject var model:Center
 @State private var draft=RemoteDraft()
 @State private var original=RemoteDraft()
 @State private var loaded=false
 @State private var saving=false
 @State private var error:String?
 @State private var revision=0
 @State private var confirmingRotate=false
 @State private var confirmingDelete=false
 var remote:RemoteAccess? { model.configuration.remoteAccess }
 var configured:Bool { remote != nil }
 var routes:[RouteGroup] { model.configuration.routes.filter{!$0.nodes.isEmpty} }
 func load() {
  model.loadEditor { ok in
   guard ok else { return }
   draft=RemoteDraft(model.configuration.remoteAccess)
   if model.configuration.remoteAccess == nil { draft.route=routes.first{$0.enabled}?.id ?? "" }
   original=draft;revision=model.configuration.revision;error=nil;loaded=true
  }
 }
 func save(rotateKeys:Bool=false) {
  saving=true;error=nil
  model.mutate(["action":"save-remote-access","revision":revision,"remote":draft.payload(rotateKeys:rotateKeys)]) { reply in
   saving=false
   if reply.ok { load() } else { error=reply.error ?? L("Не удалось сохранить изменения") }
  }
 }
 func copy(_ value:String) {
  NSPasteboard.general.clearContents();NSPasteboard.general.setString(value,forType:.string)
 }
 var statusText:(String,Color) {
  guard let remote else { return (L("Не настроен"),.secondary) }
  if !remote.enabled { return (L("Выключен"),.secondary) }
  switch model.remoteState {
  case "connected":return (L("Связь с сервером есть"),.green)
  case "stopped":return (L("Движок остановлен"),.red)
  default:return (L("Ожидание сервера"),.orange)
  }
 }
 var statusHelp:String {
  L("Показывает только связь Mac с сервером WireGuard. Работу сервиса на порту и подключение телефона libvpn не видит.")
 }
 var body:some View {
  EditorPane(saving:saving,changed:draft != original,error:error,cancel:{draft=original;error=nil},save:{save()},delete:configured ? {confirmingDelete=true}:nil,deleteTitle:L("Удалить настройку…")) {
   Form {
    if let remote {
     Section {
      Toggle(isOn:Binding(get:{remote.enabled},set:{_ in model.toggleRemote { load() }})) {
       Text(L("Удалённый доступ"))
       HStack(spacing:5) {
        Circle().fill(statusText.1).frame(width:7,height:7).accessibilityHidden(true)
        Text(statusText.0).font(.caption).foregroundStyle(.secondary)
       }.help(statusHelp)
      }.disabled(model.busy != nil || draft != original)
     }
    } else {
     Section {
      Text(L("Mac сам подключается к вашему серверу WireGuard, поэтому открывать порты на роутере не нужно. С сервера будет доступен только один локальный порт.")).fixedSize(horizontal:false,vertical:true)
     }
    }
    Section(L("Сервер WireGuard")) {
     TextField(L("Адрес"),text:$draft.address,prompt:Text("vps.example.com"))
     TextField(L("Порт"),text:$draft.port,prompt:Text("51820"))
     TextField(L("Публичный ключ сервера"),text:$draft.serverPublicKey,prompt:Text(L("Base64, 44 символа")))
     TextField(L("Адрес сервера в туннеле"),text:$draft.peerAddress,prompt:Text("10.77.0.1"))
    }
    Section {
     TextField(L("Адрес Mac в туннеле"),text:$draft.tunnelAddress,prompt:Text("10.77.0.2"))
     TextField(L("Открытый порт"),text:$draft.localPort,prompt:Text(L("Порт на 127.0.0.1")))
    } header:{ Text(L("Этот Mac")) } footer:{ Text(L("Через туннель доступен только этот порт на 127.0.0.1.")).font(.caption).foregroundStyle(.secondary) }
    Section {
     Picker(L("Подключаться через"),selection:$draft.route) {
      Text(L("Напрямую")).tag("")
      ForEach(routes) { route in Text(route.displayTitle+"  "+chainSymbols(route.nodes,servers:model.configuration.nodes)).tag(route.id) }
     }
    } footer:{ Text(L("Цепочка маршрута скрывает WireGuard и работает, даже когда сам маршрут выключен или включён байпас.")).font(.caption).foregroundStyle(.secondary) }
    if let key=remote?.publicKey,let address=remote?.tunnelAddress {
     Section {
      LabeledContent(L("Публичный ключ Mac")) {
       Text(key).font(.system(.caption,design:.monospaced)).textSelection(.enabled).lineLimit(1).truncationMode(.middle)
      }
      HStack {
       Button(L("Скопировать блок [Peer]")) { copy(wireGuardPeer(publicKey:key,tunnelAddress:address)) }
       Button(L("Скопировать ключ")) { copy(key) }
       Spacer()
       Button(L("Перевыпустить…")) { confirmingRotate=true }.disabled(draft != original || model.busy != nil)
      }
     } header:{ Text(L("Настройка сервера")) } footer:{ Text(L("Добавьте блок [Peer] в конфигурацию WireGuard на сервере.")).font(.caption).foregroundStyle(.secondary) }
    } else {
     Section { Text(L("Ключи Mac создадутся при первом сохранении. Приватный ключ остаётся на этом Mac.")).font(.caption).foregroundStyle(.secondary) }
    }
   }.formStyle(.grouped).disabled(!loaded)
  }.onAppear(perform:load)
   .alert(L("Перевыпустить ключи?"),isPresented:$confirmingRotate) {
    Button(L("Отменить"),role:.cancel) {}
    Button(L("Перевыпустить"),role:.destructive) { save(rotateKeys:true) }
   } message:{ Text(L("Доступ пропадёт, пока вы не обновите публичный ключ Mac на сервере.")) }
   .alert(L("Удалить настройку?"),isPresented:$confirmingDelete) {
    Button(L("Отменить"),role:.cancel) {}
    Button(L("Удалить"),role:.destructive) { model.mutate(["action":"delete-remote-access","revision":model.configuration.revision]) { reply in if reply.ok { load() } } }
   } message:{ Text(L("Ключи Mac будут удалены. Для нового подключения блок [Peer] на сервере придётся обновить.")) }
 }
}

struct TailnetPane:View {
 @ObservedObject var model:Center
 @State private var draft=TailnetDraft()
 @State private var original=TailnetDraft()
 @State private var loaded=false
 @State private var saving=false
 @State private var error:String?
 @State private var revision=0
 @State private var confirmingReset=false
 @State private var confirmingDelete=false
 var tailnet:Tailnet? { model.configuration.tailnet }
 var routes:[RouteGroup] { model.configuration.routes.filter{!$0.nodes.isEmpty} }
 var changed:Bool { tailnet == nil || draft != original }
 func load() {
  model.loadEditor { ok in
   guard ok else { return }
   draft=TailnetDraft(model.configuration.tailnet);original=draft;revision=model.configuration.revision;error=nil;loaded=true
  }
 }
 func save(resetLogin:Bool=false) {
  saving=true;error=nil
  model.mutate(["action":"save-tailnet","revision":revision,"tailnet":draft.payload(resetLogin:resetLogin)]) { reply in
   saving=false
   if reply.ok { load() } else { error=reply.error ?? L("Не удалось сохранить изменения") }
  }
 }
 var statusText:(String,Color) {
  guard let tailnet else { return (L("Не настроен"),.secondary) }
  if !tailnet.enabled { return (L("Выключен"),.secondary) }
  switch model.tailnetState {
  case "connected":return (L("Подключено"),.green)
  case "stopped":return (L("Движок остановлен"),.red)
  case "login":return (L("Нужен ключ авторизации"),.orange)
  default:return (L("Подключение…"),.orange)
  }
 }
 var body:some View {
  EditorPane(saving:saving,changed:changed,error:error,cancel:{draft=original;error=nil},save:{save()},delete:tailnet != nil ? {confirmingDelete=true}:nil,deleteTitle:L("Удалить настройку…")) {
   Form {
    if let tailnet {
     Section {
      Toggle(isOn:Binding(get:{tailnet.enabled},set:{_ in model.toggleTailnet { load() }})) {
       Text("Tailnet")
       HStack(spacing:5) {
        Circle().fill(statusText.1).frame(width:7,height:7).accessibilityHidden(true)
        Text(statusText.0).font(.caption).foregroundStyle(.secondary)
       }.help(L("«Подключено» значит, что сервер управления принял этот Mac и прислал карту сети. Доступность отдельных устройств libvpn не проверяет."))
      }.disabled(model.busy != nil || draft != original)
     }
    } else {
     Section {
      Text(L("Подключает Mac к сети Tailscale или Headscale. Устройства сети смогут открыть на этом Mac только порты из списка ниже.")).fixedSize(horizontal:false,vertical:true)
     }
    }
    Section {
     TextField(L("Адрес"),text:$draft.controlURL,prompt:Text("https://headscale.example.com"))
     SecureField(L("Ключ авторизации"),text:$draft.authKey,prompt:Text(draft.hasAuthKey && !draft.clearAuthKey ? L("Сохранён"):L("Ключ из Headscale или Tailscale")))
     if draft.hasAuthKey {
      Toggle(L("Удалить сохранённый ключ"),isOn:$draft.clearAuthKey).disabled(!draft.authKey.isEmpty)
     }
    } header:{ Text(L("Сервер управления")) } footer:{
     Text(L("Пустой адрес — сервер Tailscale. Ключ нужен для первого входа и хранится только в помощнике.")).font(.caption).foregroundStyle(.secondary)
    }
    Section {
     TextField(L("Имя устройства"),text:$draft.hostname,prompt:Text(L("Имя этого Mac")))
     Toggle(isOn:$draft.ephemeral) {
      Text(L("Временное устройство"))
      Text(L("Сервер удалит его из сети вскоре после отключения.")).font(.caption).foregroundStyle(.secondary)
     }
     Toggle(isOn:$draft.acceptRoutes) {
      Text(L("Принимать маршруты подсетей"))
      Text(L("Адреса подсетей, которые объявляют другие устройства, пойдут через tailnet.")).font(.caption).foregroundStyle(.secondary)
     }
     TextField(L("UDP-порт WireGuard"),text:$draft.listenPort,prompt:Text("41641"))
    } header:{ Text(L("Этот Mac")) } footer:{
     Text(L("При включённом kill switch наружу выпускается только этот UDP-порт, поэтому работают только прямые соединения: ретрансляторы DERP недоступны.")).font(.caption).foregroundStyle(.secondary)
    }
    Section {
     ForEach($draft.ports) { $port in
      HStack(spacing:8) {
       Picker(L("Протокол"),selection:$port.proto) { Text("TCP").tag("tcp");Text("UDP").tag("udp") }.labelsHidden().fixedSize().accessibilityLabel(L("Протокол"))
       TextField(L("Порт в tailnet"),text:$port.port,prompt:Text(L("Порт в tailnet"))).labelsHidden().accessibilityLabel(L("Порт в tailnet"))
       Image(systemName:"arrow.right").foregroundStyle(.secondary).accessibilityHidden(true)
       TextField(L("Порт на 127.0.0.1"),text:$port.localPort,prompt:Text(port.port.isEmpty ? L("Порт на 127.0.0.1"):port.port)).labelsHidden().accessibilityLabel(L("Порт на 127.0.0.1"))
       Button { draft.ports.removeAll{$0.id==port.id} } label:{ Image(systemName:"minus.circle") }
        .buttonStyle(.borderless).help(L("Закрыть порт")).accessibilityLabel(L("Закрыть порт"))
      }
     }
     Button(L("Открыть порт")) { draft.ports.append(TailnetPortDraft()) }.disabled(draft.ports.count>=64)
    } header:{ Text(L("Открытые порты")) } footer:{
     Text(L("Соединения на эти порты ведут на 127.0.0.1 этого Mac, остальные отклоняются. Какие устройства могут подключаться, задаёт ACL сервера управления.")).font(.caption).foregroundStyle(.secondary)
    }
    Section {
     Picker(L("Подключаться через"),selection:$draft.route) {
      Text(L("Напрямую")).tag("")
      ForEach(routes) { route in Text(route.displayTitle+"  "+chainSymbols(route.nodes,servers:model.configuration.nodes)).tag(route.id) }
     }
    } header:{ Text(L("Связь с сервером управления")) } footer:{
     Text(L("Через цепочку идёт только связь с сервером управления. Трафик между устройствами всегда идёт по WireGuard.")).font(.caption).foregroundStyle(.secondary)
    }
    if tailnet != nil {
     Section {
      Button(L("Войти заново…")) { confirmingReset=true }.disabled(draft != original || model.busy != nil)
     } footer:{ Text(L("Mac получит новую идентичность в сети. Понадобится ключ авторизации.")).font(.caption).foregroundStyle(.secondary) }
    }
   }.formStyle(.grouped).disabled(!loaded)
  }.onAppear(perform:load)
   .alert(L("Войти заново?"),isPresented:$confirmingReset) {
    Button(L("Отменить"),role:.cancel) {}
    Button(L("Войти заново"),role:.destructive) { save(resetLogin:true) }
   } message:{ Text(L("Текущее устройство останется в списке сервера, пока вы его не удалите. Адрес Mac в сети может измениться.")) }
   .alert(L("Удалить настройку?"),isPresented:$confirmingDelete) {
    Button(L("Отменить"),role:.cancel) {}
    Button(L("Удалить"),role:.destructive) { model.mutate(["action":"delete-tailnet","revision":model.configuration.revision]) { reply in if reply.ok { load() } else { error=reply.error } } }
   } message:{ Text(L("Mac выйдет из сети, его идентичность будет удалена.")) }
 }
}
