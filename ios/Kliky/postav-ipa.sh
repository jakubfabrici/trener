#!/usr/bin/env bash
# Postaví nepodpísanú Kliky.ipa.
#
#   brew install xcodegen
#   trener/ios/Kliky/postav-ipa.sh            # výsledok v trener/ios/Kliky/build
#   trener/ios/Kliky/postav-ipa.sh /tmp/kliky # alebo inam
#
# IPA je bez podpisu naschvál — podpíše ju až AltStore tvojím Apple ID priamo
# na telefóne a potom si ju sám každých 7 dní obnovuje.
#
# Zámerne `xcodebuild build`, nie `archive`: archív s CODE_SIGNING_ALLOWED=NO
# vyrába rôzne rozloženie v rôznych verziách Xcode a občas aj prázdny .app.
set -euo pipefail
cd "$(dirname "$0")"

vystup="${1:-$PWD/build}"
mkdir -p "$vystup"
vystup="$(cd "$vystup" && pwd)"

command -v xcodegen >/dev/null || { echo "Chýba xcodegen — nainštaluj: brew install xcodegen" >&2; exit 1; }

xcodegen generate

rm -rf "$vystup/Payload" "$vystup/Kliky.ipa"
xcodebuild build \
  -project Kliky.xcodeproj \
  -scheme Kliky \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -derivedDataPath "$vystup/dd" \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  CODE_SIGN_IDENTITY="" \
  CODE_SIGN_ENTITLEMENTS=""

app="$vystup/dd/Build/Products/Release-iphoneos/Kliky.app"
[ -d "$app" ] || { echo "Build neprodukoval Kliky.app" >&2; exit 1; }
# Poistka: bez týchto kľúčov appka ticho nedostane povolenia a nič nezazvoní.
for kluc in NSAlarmKitUsageDescription NSRemindersFullAccessUsageDescription; do
  /usr/bin/plutil -extract "$kluc" raw "$app/Info.plist" >/dev/null \
    || { echo "V Info.plist chýba $kluc" >&2; exit 1; }
done
[ -f "$app/Kliky" ] || { echo "V Kliky.app chýba binárka" >&2; exit 1; }

mkdir -p "$vystup/Payload"
cp -R "$app" "$vystup/Payload/"
(cd "$vystup" && zip -qry Kliky.ipa Payload)
rm -rf "$vystup/Payload"

echo
echo "Hotovo: $vystup/Kliky.ipa ($(du -h "$vystup/Kliky.ipa" | cut -f1))"
