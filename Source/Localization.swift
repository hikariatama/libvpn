import Foundation

enum AppLanguage {
 static var code:String { Bundle.main.preferredLocalizations.first == "ru" ? "ru":"en" }
}
func L(_ key:String,_ values:Any...)->String {
 var result=Bundle.main.localizedString(forKey:key,value:nil,table:nil)
 let pattern=try! NSRegularExpression(pattern:#"\{([0-9]+)\}"#)
 let matches=pattern.matches(in:result,range:NSRange(result.startIndex...,in:result))
 for match in matches.reversed() {
  guard let indexRange=Range(match.range(at:1),in:result),let index=Int(result[indexRange]),values.indices.contains(index),
   let range=Range(match.range,in:result) else { continue }
  result.replaceSubrange(range,with:String(describing:values[index]))
 }
 return result
}
