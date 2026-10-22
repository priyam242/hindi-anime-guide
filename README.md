# 🎌 Hindi Anime Guide (हिंदी एनीमे गाइड)

> **भारत में एनीमे कहाँ देखें — कानूनी एवं संपादकीय हिंदी डब गाइड**  
> Legal Anime Streaming & Hindi Dub Editorial Guide for Indian Fans.

**Hindi Anime Guide** एक आधुनिक, सर्वर-रेंडर्ड (FastAPI + Jinja2) और एसईओ-अनुकूल (SEO-friendly) संपादकीय वेब गाइड (Editorial Guide) है। यह कोई साधारण निर्देशिका ("directory") नहीं है, बल्कि भारतीय एनीमे प्रशंसकों को आधिकारिक व कानूनी ओटीटी प्लेटफॉर्म्स (Crunchyroll, Netflix, JioHotstar, Prime Video आदि) पर उपलब्ध एनीमे शीर्षकों और उनके हिंदी डबिंग संदर्भ की सटीक, निष्पक्ष और अद्यतन जानकारी प्रदान करने वाली एक विचारशील संपादकीय गाइड है।

---

## 🚀 How to Run (प्रोजेक्ट कैसे चलाएं)

### 1. पूर्वापेक्षाएँ (Prerequisites)
सुनिश्चित करें कि आपके कंप्यूटर पर **Python 3.11+** स्थापित है:
```bash
python --version
```

### 2. निर्भरताएँ इंस्टॉल करें (Install Dependencies)
उत्पादन/रनटाइम निर्भरताओं के लिए:
```bash
pip install -r requirements.txt
```
डेवलपमेंट और परीक्षण (Testing) टूल्स के लिए:
```bash
pip install -r requirements-dev.txt
```

### 3. डेवलपमेंट सर्वर शुरू करें (Run Development Server)
सुविधाजनक वन-कमांड रनर चलाएं:
```bash
python dev.py
```
*वैकल्पिक रूप से Uvicorn से सीधे चला सकते हैं:*
```bash
python -m uvicorn app.main:app --reload
```
इसके बाद अपने ब्राउज़र में खोलें: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 🎨 Design Rules & Guidelines (डिज़ाइन नियम)

प्रोजेक्ट के सभी विस्तृत संपादकीय, तकनीकी और विजुअल नियम [RULES.md](RULES.md) में प्रलेखित हैं। मुख्य डिज़ाइन सिद्धांतों का संक्षेप नीचे दिया गया है:

1. **डिफ़ॉल्ट लाइट थीम (Light Theme Default):** वेबसाइट डिफ़ॉल्ट रूप से लाइट थीम (`--bg: #FAFAF7`) में खुलती है। डार्क थीम (`--bg: #0E0E12`) केवल यूज़र द्वारा टॉगल बटन दबाने पर सक्रिय होती है और `localStorage` में सुरक्षित रहती है।
2. **कंट्रास्ट मानक (Contrast Claim):** सभी टेक्स्ट और इंटरैक्टिव घटक केवल **WCAG AA** एक्सेसिबिलिटी मानकों (न्यूनतम 4.5:1 कंट्रास्ट रेशियो) के पूर्णतः अनुकूल हैं (AAA का कोई दावा नहीं)।
3. **रीडिंग कॉलम और टाइपोग्राफी:**
   - आर्टिकल पेजों पर मुख्य पठन क्षेत्र **720px** का केंद्रित कॉलम (`max-width: 720px; margin: 0 auto;`) है।
   - पठनीयता हेतु मुख्य पैराग्राफ की लाइन-हाइट **1.85** (`line-height: 1.85`) रखी गई है।
   - सेल्फ-होस्टेड फॉन्ट्स: **Noto Serif Devanagari** और **Noto Serif** (WOFF2 प्रारूप) स्थानीय रूप से लोड होते हैं, बिना किसी बाहरी Google Fonts कॉल के।
4. **शुद्ध टाइपोग्राफिक डिज़ाइन (No Image Tags):**
   - वेबसाइट पर एनीमे पोस्टर्स या कवर्स के लिए कोई `<img>` टैग इस्तेमाल नहीं होता।
   - श्रेणी-आधारित फ्लैट रंगों (Flat Colors: Red, Slate, Ink) और बोल्ड देवनागरी शीर्षकों द्वारा टाइपोग्राफिक कार्ड्स तैयार किए गए हैं।
5. **रंगों का केंद्रीकरण (Design Tokens):**
   - सभी रंग केवल `app/static/css/tokens.css` में CSS Variables के रूप में परिभाषित हैं।
   - कोडबेस में कोई ग्रेडिएंट्स, पर्पल रंग, या फाइलों के अंदर बिखरे हुए हेक्स/RGB/HSL लिटरल्स नहीं हैं।
6. **कोई स्टार रेटिंग या स्कोर नहीं (No Ratings):**
   - संपादकीय निष्पक्षता बनाए रखने के लिए साइट पर "★", "/10" या "rating" शब्द का उपयोग पूरी तरह वर्जित है।
7. **कानूनी व नो-पायरेसी नीति (Strictly Official Links):**
   - केवल वैध आधिकारिक स्ट्रीमिंग प्लेटफॉर्म्स (Crunchyroll, Netflix, JioHotstar, Prime Video) के होमपेज/ऑफिशियल URL लिंक किए जाते हैं।
   - किसी भी अनधिकृत या पायरेटेड स्रोत का संदर्भ नहीं दिया जाता।
8. **उत्पादन सुरक्षा गार्ड (Production Safety):**
   - जब `ENV=production` हो और डेटाबेस में कोई नमूना लेख (`is_sample=True`) मौजूद हो, तो एप्लिकेशन शुरू होने से इनकार कर देता है (`RuntimeError`)।
   - सभी नमूना लेख सर्च इंजन इंडेक्सिंग से सुरक्षित रखने के लिए `noindex,nofollow` मेटा टैग के साथ आते हैं।

---

## ⚙️ Tailwind CSS v3.4.17 व CSS Rebuild

- **वर्जन सूचना:** यह प्रोजेक्ट स्टैंडअलोन **Tailwind CSS v3.4.17** सीएलआई (`bin/tailwindcss.exe`) का उपयोग करता है। **इसे Tailwind v4 में अपग्रेड न करें।**
- **सीएसएस पुनः कंपाइल करना (How to Rebuild CSS):**  
  यदि आप टेम्पलेट्स या स्टाइलशीट में बदलाव करते हैं, तो टर्मिनल में निम्न कमांड चलाकर CSS रीबिल्ड करें:
  ```bash
  .\bin\tailwindcss.exe -i .\app\static\css\input.css -o .\app\static\css\tailwind.css --minify
  ```

---

## 📁 प्रोजेक्ट संरचना (Project Structure)

```text
Anime/
├── .env                      # स्थानीय विकास पर्यावरण कॉन्फ़िगरेशन
├── .env.example              # पर्यावरण कॉन्फ़िगरेशन टेम्पलेट
├── dev.py                    # वन-कमांड डेवलपमेंट सर्वर रनर
├── requirements.txt          # पिन किए गए सटीक उत्पादन पैकेज (==)
├── requirements-dev.txt      # डेवलपमेंट और टेस्टिंग पैकेज (-r requirements.txt, pytest, httpx)
├── tailwind.config.js        # टेलविंड सीएसएस कॉन्फ़िगरेशन
├── alembic.ini               # डेटाबेस माइग्रेशन कॉन्फ़िगरेशन
├── README.md                 # यह मुख्य दस्तावेज़ीकरण फ़ाइल
├── bin/
│   └── tailwindcss.exe       # स्टैंडअलोन Tailwind v3.4.17 CLI (डू नॉट अपग्रेड)
├── app/
│   ├── main.py               # FastAPI ऐप, लाइफस्पैन और एक्सेप्शन हैंडलर्स
│   ├── config.py             # Pydantic Settings और SITE_NAME = "Hindi Anime Guide"
│   ├── templates_config.py   # Jinja2Templates इंस्टैंस और हिंदी डेट फ़िल्टर
│   ├── db.py                 # SQLite इंजन, WAL मोड, सीडिंग और प्रोडक्शन गार्ड
│   ├── models.py             # SQLModel स्कीमा (Category, Article, Platform, Availability)
│   ├── markdown_utils.py     # nh3 सैनिटाइज्ड मार्कडाउन और सुरक्षित ऑटो-TOC
│   ├── routers/
│   │   └── pages.py          # होम, आर्टिकल, कैटेगरी और फूटर प्लेसहोल्डर राउट्स
│   ├── templates/
│   │   ├── base.html         # मुख्य लेआउट (साइडबार, टॉपबार, थीम टॉगल, inert मोबाइल मेन्यू)
│   │   ├── home.html         # होमपेज (संपादकीय ग्रिड और खाली स्थिति "पहले लेख जल्द आ रहे हैं")
│   │   ├── article.html      # 720px रीडिंग कॉलम, TOC, सारांश बॉक्स और उपलब्धता टेबल
│   │   ├── category.html     # श्रेणी सूची पेज
│   │   ├── placeholder.html  # फूटर लिंक्स (/about, /contact, /privacy, /disclaimer) हेतु noindex पेज
│   │   ├── 404.html          # कस्टम 404 त्रुटि पेज
│   │   └── components/
│   │       └── anime_card.html # शुद्ध टाइपोग्राफिक एनीमे कार्ड कंपोनेंट
│   └── static/
│       ├── favicon.svg       # नो-इमेज एसवीजी फेविकॉन
│       ├── css/
│       │   ├── tokens.css    # सभी अधिकृत कलर टोकन्स और थीम वैरिएबल्स
│       │   ├── fonts.css     # @font-face परिभाषाएँ (स्थानीय फॉन्ट्स)
│       │   ├── input.css     # टेलविंड इनपुट + कस्टम टाइपोग्राफी व मोशन-रिड्यूस
│       │   └── tailwind.css  # कंपाइल्ड मिनिफाइड सीएसएस
│       ├── fonts/            # सेल्फ-होस्टेड Noto Serif देवनागरी व लैटिन WOFF2 फाइल्स
│       │   ├── noto-serif-devanagari-variable.woff2
│       │   ├── noto-serif-latin-regular.woff2
│       │   └── noto-serif-latin-bold.woff2
│       └── js/
│           └── alpine.min.js # स्टैंडअलोन Alpine.js 3.14 क्लाइंट लाइब्रेरी
└── tests/
    ├── test_pages.py         # एंडपॉइंट्स, मेटाडेटा, फूटर और ब्रांडिंग टेस्ट्स
    └── test_rules.py         # RULES.md के सभी कड़े नियमों की स्वचालित सत्यापन सुइट
```

---

## 🧪 स्वचालित परीक्षण (Run Tests)

सभी सुरक्षा, डिज़ाइन और कार्यात्मक नियमों का परीक्षण करने के लिए:
```bash
python -m pytest
```
*परीक्षण सुइट में शामिल हैं:*
- `test_rules.py`: कोई `<img>` टैग न होना, कोई स्टार/रेटिंग न होना, कलर लिटरल्स प्रतिबंध, खाली डेटाबेस पर साफ संदेश, आर्टिकल उपलब्धता नियम, प्रोडक्शन सेफ्टी गार्ड और `noindex` सत्यापन।
- `test_pages.py`: सभी मुख्य पेजों की HTTP स्थिति, नेविगेशन, थीम टॉगल, 404 हैंडलर और मेटाडेटा शुद्धता।

---

## 🔑 Google Login Setup (गूगल लॉगिन सेटअप)

1. [Google Cloud Console](https://console.cloud.google.com/) में एक नया OAuth 2.0 Client ID बनाएं (Application type: Web application)।
2. **Authorized redirect URIs** में दर्ज करें:
   ```
   {PUBLIC_BASE_URL}/auth/google/callback
   ```
   (स्थानीय परीक्षण हेतु: `http://127.0.0.1:8000/auth/google/callback`)
3. अपनी `.env` फ़ाइल में क्रेडेंशियल्स जोड़ें:
   ```env
   GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
   GOOGLE_CLIENT_SECRET="your-client-secret"
   PUBLIC_BASE_URL="http://127.0.0.1:8000"
   ```

---

## ☁️ Render Deployment (रेंडर पर डिप्लॉयमेंट)

वेबसाइट को [Render](https://render.com/) (Web Service) पर डिप्लॉय करने के लिए आवश्यक सेटिंग्स:

### 1. स्टार्ट कमांड (Start Command)
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"
```

### 2. पर्यावरण चर (Environment Variables)
Render डैशबोर्ड में Environment टैब के अंतर्गत निम्न वेरिएबल्स सेट करें:
- `ENV`: `production`
- `APP_ENV`: `production`
- `PUBLIC_BASE_URL`: `https://hindi-anime-guide.onrender.com`
- `BREVO_API_KEY`: आपकी Brevo API Key
- `MAIL_FROM_EMAIL`: आपका सत्यापित प्रेषक ईमेल (उदा. `contact@yourdomain.com`)
- `GOOGLE_CLIENT_ID`: आपका Google OAuth क्लाइंट आईडी
- `GOOGLE_CLIENT_SECRET`: आपका Google OAuth क्लाइंट सीक्रेट

> **महत्वपूर्ण सूचना (SMTP ब्लॉकेज):** Render का फ्री प्लान आउटबाउंड SMTP पोर्ट्स (25, 465, 587) को ब्लॉक करता है। इसलिए ईमेल OTP और Google लॉगिन डिवाइस सत्यापन के लिए HTTPS API (पोर्ट 443) आधारित प्रदाता जैसे **Brevo** या **Resend** का उपयोग अनिवार्य है।

### 3. Google OAuth Redirect URI
[Google Cloud Console](https://console.cloud.google.com/) के Authorized redirect URIs में निम्न URL जोड़ें:
```
https://hindi-anime-guide.onrender.com/auth/google/callback
```