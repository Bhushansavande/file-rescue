# File Rescue 🛟

**डिलीट झालेले फोटो, व्हिडिओ, गाणी आणि APK फाइल्स परत मिळवणारे मोफत, ओपन-सोर्स टूल.**
लॅपटॉप (Windows/Linux), SD कार्ड, पेन ड्राइव्ह आणि Android फोनसाठी.

> ⚠️ **सगळ्यात महत्त्वाचे:** फाइल डिलीट झाल्यावर ज्या ड्राइव्हमधून परत मिळवायची आहे त्यावर **काहीही नवीन सेव्ह करू नका**. जितके जास्त वापराल, तितकी जुनी फाइल कायमची पुसली जाण्याची शक्यता जास्त. आधी `docs/SHIKSHAN.md` वाचा.

## हे काय करते?

| सुविधा | कमांड | कुठे उपयोगी |
|---|---|---|
| **File carving** (डेटावरून फाइल शोधणे) | `filerescue carve` | SD कार्ड, पेन ड्राइव्ह, HDD, डिस्क इमेज |
| **Recycle Bin रिस्टोअर** (मूळ नावासह) | `filerescue recyclebin` | Windows लॅपटॉप |
| **Android ट्रॅश फाइल्स** (ADB द्वारे) | `filerescue android` | Android 11+ फोन |
| **सुरक्षित इमेज प्रत** | `filerescue image` | कोणताही ड्राइव्ह, आधी प्रत मग रिकव्हरी |

**समर्थित फाइल प्रकार:** JPG, PNG, GIF, WEBP, MP4, MOV, 3GP, M4A, MP3, WAV, AVI, **APK**, PDF, DOCX, XLSX, PPTX, ZIP.

सर्व काही **फक्त वाचन (read-only)** पद्धतीने चालते: स्रोत ड्राइव्हमध्ये काहीही लिहिले जात नाही. बाहेरील कोणत्याही लायब्ररीची गरज नाही (फक्त Python 3.8+).

## इन्स्टॉल

```bash
git clone https://github.com/<तुमचे-युजरनेम>/file-rescue.git
cd file-rescue
pip install .
```

इन्स्टॉल न करताही चालते: `python -m filerescue ...`

## वापर

### 1) SD कार्ड / पेन ड्राइव्ह / डिस्कमधून (सगळ्यात प्रभावी)

आउटपुट फोल्डर **वेगळ्या ड्राइव्हवर** असावा.

```bash
# Windows (Administrator म्हणून Command Prompt उघडा; E: हा कार्ड ड्राइव्ह)
filerescue carve \\.\E: -o D:\recovered --types photos,videos

# Linux
sudo filerescue carve /dev/sdb1 -o ~/recovered --types jpg,mp3,apk

# डिस्क इमेज फाइलवर
filerescue carve disk.img -o recovered
```

आधी सुरक्षित प्रत बनवणे उत्तम (खराब होत असलेल्या कार्डसाठी तर खूपच):

```bash
filerescue image \\.\E: D:\sdcard.img
filerescue carve D:\sdcard.img -o D:\recovered
```

`--types` उदाहरणे: `jpg,png` · `photos` · `videos` · `music` · `apps` (APK) · `documents`

आउटपुट असे फोल्डर्समध्ये लावले जाते: `photos/ videos/ music/ apps/ documents/ archives/`.
`_partial` असलेल्या फाइल्स अर्धवट असू शकतात (शेवटचा भाग ओव्हरराईट झालेला).

### 2) Windows Recycle Bin

```bash
filerescue recyclebin                  # यादी: मूळ पथ, आकार, डिलीट वेळ
filerescue recyclebin -o D:\restored   # मूळ नावासह कॉपी
```

### 3) Android फोन

1. फोनमध्ये **Developer options → USB debugging** चालू करा.
2. लॅपटॉपवर [Android platform-tools](https://developer.android.com/tools/releases/platform-tools) (मोफत) डाउनलोड करून `adb` PATH मध्ये टाका.
3. फोन USB ने जोडा, फोनवर परवानगी द्या, मग:

```bash
filerescue android                  # ट्रॅश फाइल्सची यादी
filerescue android -o D:\phone-trash
```

> फोनची **अंतर्गत स्टोरेज** (एन्क्रिप्टेड असते) थेट carve करता येत नाही. त्यासाठी `docs/SHIKSHAN.md` मधील Android भाग वाचा. **फोनचा SD कार्ड** मात्र कार्ड रीडरने जोडून पद्धत 1 ने स्कॅन करता येतो.

## मर्यादा (प्रामाणिकपणे)

- **SSD आणि नवीन फोन** TRIM मुळे डिलीट डेटा पटकन खरोखर पुसतात; तिथे carving बहुतेक वेळा काम करत नाही.
- Carving फाइलचे **नाव आणि फोल्डर** परत देऊ शकत नाही (ती माहिती डिलीटसोबत जाते), फक्त आशय मिळतो.
- **तुकड्या-तुकड्यात (fragmented)** साठवलेल्या मोठ्या फाइल्स अर्धवट/खराब मिळू शकतात.
- MP3 फक्त `ID3` टॅग असलेली गाणी ओळखते.
- हे एक सुरुवातीचे (v0.1) टूल आहे. गंभीर/महत्त्वाच्या डेटासाठी **PhotoRec/TestDisk** (मोफत, ओपन-सोर्स) सुद्धा वापरून पाहा.

## टेस्ट्स

```bash
python -m unittest discover -s tests -v
```

## योगदान

सुधारणा, नवीन फाइल प्रकार आणि भाषांतर यांचे स्वागत आहे: `CONTRIBUTING.md` पहा.
पुढील योजना: `docs/YOJANA.md`.

## परवाना

MIT: मोफत वापरा, बदला, शेअर करा. तुमच्या स्वतःच्या किंवा परवानगी असलेल्या डिव्हाइसवरच वापरा.

---
**Life Style Knowledge Hub (LSKH)** · Bhushan Savande
