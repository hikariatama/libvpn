import SwiftUI

struct ServerEditor:View {
 @ObservedObject var model:Center
 @Environment(\.dismiss) private var dismiss
 @State var session:EditorSession<ServerDraft>
 @State private var useLink=false
 @State private var advanced=false
 @ViewBuilder func field(_ title:String,_ value:Binding<String>,secure:Bool=false,prompt:String="")->some View {
  PreferenceField(title:title) {
   if secure { SecureField(title,text:value,prompt:Text(prompt)).labelsHidden().accessibilityLabel(title) }
   else { TextField(title,text:value,prompt:Text(prompt)).labelsHidden().accessibilityLabel(title) }
  }
 }
 func save() {
  session.saving=true;session.error=nil
  let payload:[String:Any]=useLink ? ["uri":session.draft.uri]:session.draft.payload
  model.mutate(["action":"save-node","revision":session.revision,"node":payload]) { reply in
   if session.finish(ok:reply.ok,error:reply.error) { dismiss() }
  }
 }
 var body:some View {
  EditorSheet(title:session.draft.hasUUID ? L("Изменить сервер"):L("Новый сервер"),saving:session.saving,error:session.error,cancel:{dismiss()},save:save) {
   EditorForm {
    if !session.draft.hasUUID {
     PreferenceField(title:L("Добавить")) {
      Picker(L("Добавить"),selection:$useLink) { Text(L("Вручную")).tag(false);Text(L("Из ссылки VLESS")).tag(true) }.labelsHidden()
       .onChange(of:useLink) { _,value in if !value { session.draft.uri="" } }
     }
    }
    if useLink { field(L("Ссылка"),$session.draft.uri,secure:true,prompt:"vless://…") }
    else {
     PreferenceSection {
      field(L("Название"),$session.draft.name)
      PreferenceField(title:L("Страна")) { CountryPicker(selection:$session.draft.country).labelsHidden() }
     }
     PreferenceSection(title:L("Подключение")) {
      field(L("Адрес"),$session.draft.address,prompt:"example.com")
      field(L("Порт"),$session.draft.port)
      field("UUID",$session.draft.uuid,secure:true,prompt:session.draft.hasUUID ? L("Не изменять"):L("UUID сервера"))
     }
     PreferenceSection(title:"TLS") {
      field(L("Имя сервера"),$session.draft.serverName,prompt:session.draft.address)
      field("Pin SHA-256",$session.draft.pin,secure:true,prompt:session.draft.hasPin ? L("Не изменять"):L("Системные CA")).disabled(session.draft.clearPin)
      if session.draft.hasPin {
       Toggle(L("Удалить сохранённый pin"),isOn:$session.draft.clearPin).padding(.leading,PreferenceFieldMetrics.inset)
        .onChange(of:session.draft.clearPin) { _,value in if value { session.draft.pin="" } }
      }
      DisclosureGroup(L("Версии TLS"),isExpanded:$advanced) {
       VStack(spacing:9) {
        PreferenceField(title:L("Минимальная")) {
         Picker(L("Минимальная версия TLS"),selection:$session.draft.minTLS) {
          Text(L("Авто")).tag("");Text("1.2").tag("1.2");Text("1.3").tag("1.3")
         }.labelsHidden()
        }
        PreferenceField(title:L("Максимальная")) {
         Picker(L("Максимальная версия TLS"),selection:$session.draft.maxTLS) {
          Text(L("Авто")).tag("");Text("1.2").tag("1.2");Text("1.3").tag("1.3")
         }.labelsHidden()
        }
       }.padding(.top,8)
      }
     }
    }
   }
  }.frame(width:500,height:useLink ? 220:(session.draft.hasUUID ? 450:490)).interactiveDismissDisabled(session.saving)
 }
}

struct GroupEditor:View {
 @ObservedObject var model:Center
 @Environment(\.dismiss) private var dismiss
 @State var session:EditorSession<RouteGroup>
 @State private var domains:String
 @State private var suffixes:String
 @State private var cidrs:String
 @State private var matcher="domains"
 @State private var showMatches:Bool
 init(model:Center,group:RouteGroup,revision:Int) {
  self.model=model
  _session=State(initialValue:EditorSession(original:group,draft:group,revision:revision))
  _domains=State(initialValue:(group.domains ?? []).joined(separator:"\n"))
  _suffixes=State(initialValue:(group.suffixes ?? []).joined(separator:"\n"))
  _cidrs=State(initialValue:(group.cidrs ?? []).joined(separator:"\n"))
  _showMatches=State(initialValue:group.title.isEmpty)
 }
 var rulesBinding:Binding<String> {
  switch matcher { case "suffixes":return $suffixes;case "cidrs":return $cidrs;default:return $domains }
 }
 var matcherTitle:String { matcher=="domains" ? L("Точные домены"):matcher=="suffixes" ? L("Домены и поддомены"):"IP / CIDR" }
 func save() {
  if session.draft.id != "fallback" {
   session.draft.domains=lines(domains);session.draft.suffixes=lines(suffixes);session.draft.cidrs=lines(cidrs)
  }
  session.saving=true;session.error=nil
  model.mutate(["action":"save-group","revision":session.revision,"group":session.draft.payload]) { reply in
   if session.finish(ok:reply.ok,error:reply.error) { dismiss() }
  }
 }
 var body:some View {
  EditorSheet(title:session.draft.title.isEmpty ? L("Новое правило"):L("Изменить правило"),saving:session.saving,error:session.error,cancel:{dismiss()},save:save) {
   EditorForm {
    PreferenceSection {
     PreferenceField(title:L("Название")) {
      HStack(spacing:8) {
       TextField("",text:$session.draft.flag,prompt:Text("↗")).labelsHidden()
        .multilineTextAlignment(.center).frame(width:34).help(L("Значок правила")).accessibilityLabel(L("Значок правила"))
       TextField(L("Название"),text:$session.draft.title).labelsHidden().accessibilityLabel(L("Название правила"))
      }
     }
     VStack(alignment:.leading,spacing:8) {
      Toggle(L("Включить цепочку"),isOn:$session.draft.enabled)
      Toggle("Kill switch",isOn:Binding(get:{session.draft.protected},set:{session.draft.killSwitch=$0}))
       .help(L("Отключённая цепочка блокирует трафик правила. При аварии движка закрывается весь внешний выход."))
     }.padding(.leading,PreferenceFieldMetrics.inset)
    }
    PreferenceSection(title:L("Цепочка"),alignedToFields:false) {
     EditorChain(servers:model.configuration.nodes,nodes:$session.draft.nodes)
      .frame(height:CGFloat(min(max(session.draft.nodes.count,2),3))*28+38)
    }
    if session.draft.id != "fallback" {
     DisclosureGroup(L("Условия"),isExpanded:$showMatches) {
      VStack(alignment:.leading,spacing:10) {
       PreferenceField(title:L("Тип")) {
        Picker(L("Тип условия"),selection:$matcher) {
         Text(L("Точные домены")).tag("domains");Text(L("Домены и поддомены")).tag("suffixes");Text("IP / CIDR").tag("cidrs")
        }.labelsHidden()
       }
       TextEditor(text:rulesBinding).font(.system(.body,design:.monospaced))
        .frame(height:110).accessibilityLabel(matcherTitle)
        .border(Color(nsColor:.separatorColor))
        .help(L("Один адрес на строку. Любое совпадение выбирает это правило."))
       if !(session.draft.snapshots ?? []).isEmpty {
        DisclosureGroup(L("Наборы правил")) {
         ForEach(session.draft.snapshots ?? [],id:\.self) { digest in
          HStack {
           Text(String(digest.prefix(12))+"…").font(.system(.caption,design:.monospaced))
           Spacer()
           Button { session.draft.snapshots?.removeAll{$0==digest} } label:{Image(systemName:"minus.circle")}
            .buttonStyle(.borderless).accessibilityLabel(L("Удалить набор правил"))
          }
         }
        }
       }
      }.padding(.top,10)
     }
    }
   }.toggleStyle(.checkbox)
  }.frame(width:520,height:showMatches && session.draft.id != "fallback" ? 520:400).interactiveDismissDisabled(session.saving)
 }
}
