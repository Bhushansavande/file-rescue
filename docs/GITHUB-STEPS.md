# GitHub वर प्रोजेक्ट टाकण्याच्या स्टेप्स (सुरुवातीपासून)

## भाग A: एकदाच करायची तयारी

1. **Git इन्स्टॉल करा:** git-scm.com/downloads (Windows साठी "Git for Windows").
2. **GitHub खाते:** github.com वर Sign up (मोफत).
3. Command Prompt / PowerShell उघडून तुमची ओळख सांगा:
   ```bash
   git config --global user.name "Bhushan Savande"
   git config --global user.email "तुमचा-GitHub-ईमेल"
   ```

## भाग B: GitHub वर रिकामी repo बनवा

1. github.com वर लॉगिन → वर उजवीकडे **+** → **New repository**
2. **Repository name:** `file-rescue`
3. **Description:** `डिलीट झालेले फोटो, व्हिडिओ, गाणी, APK परत मिळवणारे मोफत ओपन-सोर्स टूल`
4. **Public** निवडा (ओपन-सोर्ससाठी).
5. ❗ **"Add a README", ".gitignore", "license" हे तीनही चेकबॉक्स रिकामे ठेवा.** (हे सगळे फाइल्स आधीच प्रोजेक्टमध्ये आहेत.)
6. **Create repository** दाबा. पुढच्या पानावर दिसणारा पत्ता `https://github.com/<युजरनेम>/file-rescue.git` लक्षात ठेवा.

## भाग C: लॅपटॉपवरून अपलोड

zip अनझिप करा, मग त्या `file-rescue` फोल्डरमध्ये टर्मिनल उघडा:

```bash
# आधी टेस्ट्स चालवून पाहा (Python 3.8+ लागेल)
python -m unittest discover -s tests

git init
git add .
git commit -m "v0.1: File Rescue, carving, Recycle Bin, Android ट्रॅश"
git branch -M main
git remote add origin https://github.com/<तुमचे-युजरनेम>/file-rescue.git
git push -u origin main
```

पहिल्यांदा push करताना GitHub लॉगिन विंडो येते: ब्राउझरने लॉगिन करा. (पासवर्डऐवजी Personal Access Token मागितला तर: GitHub → Settings → Developer settings → Personal access tokens → Generate, `repo` परवानगी द्या.)

## भाग D: repo सजवा (5 मिनिटे)

1. repo पानावर उजवीकडे ⚙️ (About) → **Topics** मध्ये टाका: `data-recovery`, `file-recovery`, `file-carving`, `android`, `python`, `marathi`, `open-source`.
2. **Releases → Create a new release** → Tag `v0.1.0`, शीर्षक "v0.1: पहिली आवृत्ती".
3. README मधील `<तुमचे-युजरनेम>` तुमच्या खऱ्या युजरनेमने बदला.

## भाग E: पुढे बदल करताना

```bash
git add .
git commit -m "काय बदलले ते थोडक्यात"
git push
```

**नियमित सवयी:**
- लहान, वारंवार commits करा; संदेश स्पष्ट लिहा.
- नवीन फीचरसाठी वेगळी branch: `git checkout -b feature/heic-support`.
- कोणतेही रिकव्हर केलेले फोटो/इमेज/पासवर्ड/टोकन कधीही commit करू नका.

## सामान्य अडचणी

| अडचण | उपाय |
|---|---|
| `git: command not found` | Git इन्स्टॉल केल्यानंतर टर्मिनल पुन्हा उघडा |
| `remote origin already exists` | `git remote set-url origin <नवीन-URL>` |
| `rejected ... fetch first` | repo बनवताना README निवडला असेल: `git pull origin main --allow-unrelated-histories` मग पुन्हा `git push` |
| `Permission denied` | टोकन/लॉगिन तपासा, युजरनेम बरोबर आहे का ते पाहा |
