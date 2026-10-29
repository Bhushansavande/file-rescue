#!/bin/sh
# One-time setup: creates the Android project files around lib/ and adds the storage permissions.
# Needs Flutter (https://docs.flutter.dev/get-started/install). Run it inside android_app/.
set -e
flutter create --platforms=android --org com.lskh --project-name file_rescue_app .
rm -f test/widget_test.dart   # the generated sample test refers to a class this app does not have
MANIFEST=android/app/src/main/AndroidManifest.xml
if ! grep -q MANAGE_EXTERNAL_STORAGE "$MANIFEST"; then
  sed -i 's#<application#<uses-permission android:name="android.permission.MANAGE_EXTERNAL_STORAGE"/>\n    <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" android:maxSdkVersion="32"/>\n    <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE" android:maxSdkVersion="32"/>\n    <application#' "$MANIFEST"
fi
flutter pub get
echo "Done. Build with: flutter build apk --release"
