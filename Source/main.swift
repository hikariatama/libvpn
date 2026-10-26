import Cocoa
import SwiftUI

final class Delegate:NSObject,NSApplicationDelegate,NSPopoverDelegate {
 var item:NSStatusItem!
 let popover=NSPopover()
 var model:Center!
 var settings:NSWindow?
 func applicationDidFinishLaunching(_ notification:Notification) {
  NSApp.setActivationPolicy(.accessory)
  configureMenus()
  model=Center();model.openSettings={ [weak self] in self?.showSettings() }
  if model.preview { NSApp.appearance=NSAppearance(named:.darkAqua) }
  let host=NSHostingController(rootView:PopoverContent(model:model));host.sizingOptions=[.preferredContentSize]
  popover.contentViewController=host;popover.delegate=self;popover.behavior = .transient
  item=NSStatusBar.system.statusItem(withLength:28)
  item.button?.image=menuBarImage(size:20)
  item.button?.toolTip=L("libvpn — маршрутизация");item.button?.setAccessibilityLabel(L("libvpn — маршрутизация"))
  item.button?.target=self;item.button?.action=#selector(toggle)
  DispatchQueue.main.asyncAfter(deadline:.now()+0.5) {
   if self.model.preview && ProcessInfo.processInfo.arguments.contains("--preview-settings") { self.showSettings() }
   else { self.toggle() }
  }
 }
 func configureMenus() {
  let main=NSMenu()
  let application=NSMenuItem();let appMenu=NSMenu(title:"libvpn")
  let preferences=NSMenuItem(title:L("Настройки…"),action:#selector(showSettings),keyEquivalent:",");preferences.target=self
  appMenu.addItem(preferences);appMenu.addItem(.separator())
  appMenu.addItem(withTitle:L("Скрыть libvpn"),action:#selector(NSApplication.hide(_:)),keyEquivalent:"h")
  appMenu.addItem(withTitle:L("Завершить libvpn"),action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q")
  application.submenu=appMenu;main.addItem(application)
  let editItem=NSMenuItem();let edit=NSMenu(title:L("Правка"))
  for (title,action,key) in [(L("Отменить"),"undo:","z"),(L("Повторить"),"redo:","Z"),(L("Вырезать"),"cut:","x"),(L("Скопировать"),"copy:","c"),(L("Вставить"),"paste:","v"),(L("Выбрать всё"),"selectAll:","a")] {
   edit.addItem(withTitle:title,action:Selector(action),keyEquivalent:key)
  }
  editItem.submenu=edit;main.addItem(editItem)
  let windowItem=NSMenuItem();let windows=NSMenu(title:L("Окно"))
  windows.addItem(withTitle:L("Закрыть"),action:#selector(NSWindow.performClose(_:)),keyEquivalent:"w")
  windows.addItem(withTitle:L("Свернуть"),action:#selector(NSWindow.performMiniaturize(_:)),keyEquivalent:"m")
  windowItem.submenu=windows;main.addItem(windowItem);NSApp.windowsMenu=windows
  NSApp.mainMenu=main
 }
 @objc func showSettings() {
  popover.performClose(nil)
  if settings == nil {
   let window=NSWindow(contentRect:NSRect(x:0,y:0,width:520,height:300),styleMask:[.titled,.closable],backing:.buffered,defer:false)
   window.title=L("Настройки libvpn");window.isReleasedWhenClosed=false
   window.toolbarStyle = .preference
   window.contentViewController=SettingsController(model:model)
   window.center();settings=window
  }
  settings?.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)
 }
 func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows flag:Bool)->Bool { if !popover.isShown { toggle() };return true }
 func popoverDidClose(_ notification:Notification) { model.visible=false }
 @objc func toggle() {
  if popover.isShown { popover.performClose(nil) }
  else if let button=item.button {
   model.visible=true;model.refresh();model.refreshMetrics()
   popover.show(relativeTo:button.bounds,of:button,preferredEdge:.minY)
   NSApp.activate(ignoringOtherApps:true);popover.contentViewController?.view.window?.makeKey()
  }
 }
}
let app=NSApplication.shared
let delegate=Delegate();app.delegate=delegate;app.run()
