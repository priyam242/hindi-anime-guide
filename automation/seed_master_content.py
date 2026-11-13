"""Master content populator for Hindi Anime Guide.

Ingests:
1. Top Manga updates into AnimeDraft (content_type='manga')
2. Anime Awards into AnimeDraft (content_type='award')
3. High-quality editorial articles across all major categories into Article & relationships.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select
from app.db import engine
from app.models import Article, AnimeDraft, Category, Platform, Availability, ArticleCategoryLink
from app.routers.pages import invalidate_public_caches

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("automation.master_content")

NOW = datetime.now(timezone.utc)

# -------------------------------------------------------------
# 1. MANGA UPDATES
# -------------------------------------------------------------
MANGA_ITEMS = [
    {
        "external_id": "mangadex-one-piece-elbaf",
        "title_en": "One Piece: Elbaf Island Arc & The Secrets of Joyboy",
        "title_hi": "वन पीस: एल्बाफ आर्क और रहस्यमय जोयबॉय का इतिहास",
        "summary_en": "Eiichiro Oda's epic masterpiece enters the legendary land of warriors, Elbaf. Luffy, the Straw Hat Pirates, and the giants confront new revelations regarding the Void Century, ancient weapons, and the final race for the One Piece treasure. Chapter releases continue weekly on MangaPlus and Viz Media with worldwide digital distribution.",
        "summary_hi": "एइचिरो ओडा का महाकाव्य मंगा 'वन पीस' योद्धाओं की पौराणिक भूमि एल्बाफ में प्रवेश कर चुका है। लफी, स्ट्रॉ हैट पाइरेट्स और दिग्गज योद्धा मिलकर वॉयड सेंचुरी, प्राचीन हथियारों और जोयबॉय के अनसुलझे रहस्यों का सामना कर रहे हैं। भारतीय प्रशंसक आधिकारिक मंगाप्लस ऐप और विज़ मीडिया पर हर हफ़्ते नए चैप्टर्स पढ़ सकते हैं।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx30013-ulH2jPCH2iO9.png",
        "source_url": "https://mangadex.org/title/a1c7c817-4e59-43b7-9365-09675a149a6f",
    },
    {
        "external_id": "mangadex-jjk-shinjuku",
        "title_en": "Jujutsu Kaisen: Shinjuku Showdown & The Curse King Climax",
        "title_hi": "जुजुत्सु काइसेन: शिंजुकु शोडाउन और सुकुना का अंतिम युद्ध",
        "summary_en": "Gege Akutami's dark fantasy manga reaches its monumental crescendo in the Shinjuku Showdown. Sorcerers from Jujutsu High unite their domain expansions against Ryomen Sukuna, delivering unprecedented combat choreographies, emotional farewells, and defining modern Shonen Jump history.",
        "summary_hi": "गेगे अकुतामी का डार्क फैंटेसी मंगा 'जुजुत्सु काइसेन' अपने सबसे ऐतिहासिक पड़ाव 'शिंजुकु शोडाउन' पर पहुँच चुका है। जुजुत्सु हाई के सॉसरर्स ने कर्स किंग सुकुना के खिलाफ अपने सभी डोमेन एक्सपेंशन और प्राचीन तकनीकों को झोंक दिया है। यह आर्क आधुनिक शोनेन मंगा इतिहास की सबसे तीव्र और रोमांचक लड़ाइयों में गिना जा रहा है।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx101517-H3F5H7wmD5iK.png",
        "source_url": "https://mangadex.org/title/c52b2ce3-7f95-469c-96b0-479524fb7a1a",
    },
    {
        "external_id": "mangadex-csm-part2",
        "title_en": "Chainsaw Man Part 2: Asa Mitaka & The War Devil Apocalypse",
        "title_hi": "चेनसॉ मैन भाग 2: असा मिताका और वॉर डेविल की कहानी",
        "summary_en": "Tatsuki Fujimoto's psychological horror continues in Chainsaw Man Part 2 (Academy Saga). High school student Asa Mitaka forms an uneasy symbiotic contract with the War Devil Yoru, pursuing Denji amidst devil attacks, apocalyptic omens, and unpredictable dark comedy.",
        "summary_hi": "तात्सुकी फुजिमोटो का बहुचर्चित साइकोलॉजिकल मंगा 'चेनसॉ मैन' पार्ट 2 (अकादमी सागा) में नई ऊंचाइयाँ छू रहा है। हाई स्कूल की छात्रा असा मिताका और वॉर डेविल 'योरू' के बीच का रहस्यमय अनुबंध, डेंजी के सामान्य जीवन की चाहत और डेविल्स के विनाशकारी हमलों के बीच की यह कहानी बेहद अनोखी और अप्रत्याशित है।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx105778-9MhWJb27sP8h.png",
        "source_url": "https://mangadex.org/title/a77742c1-fda6-40e3-980f-930f82092c0a",
    },
    {
        "external_id": "mangadex-spy-family-latest",
        "title_en": "SPY x FAMILY: Operation Strix & The Forger Family Missions",
        "title_hi": "स्पाई x फैमिली: आन्या और ल्वॉयड का नया सीक्रेट मिशन",
        "summary_en": "Tatsuya Endo's award-winning comedy manga deepens Operation Strix. Master spy Twilight, assassin Yor, telepathic Anya, and precognitive dog Bond navigate high-society Ostania, covert counter-intelligence, and school exams with heartwarming humor and stylish espionage.",
        "summary_hi": "तात्सुया एंडो का लोकप्रिय पारिवारिक कॉमेडी मंगा 'स्पाई x फैमिली' अपने मिशन 'ऑपरेशन स्ट्रिक्स' के नए चरणों में प्रवेश कर चुका है। मास्टर जासूस ट्विलाइट (ल्वॉयड), कातिल योर, मन पढ़ने वाली प्यारी आन्या और भविष्य देखने वाला कुत्ता बॉन्ड मिलकर जासूसी और पारिवारिक जीवन की मजेदार घटनाओं को पेश करते हैं।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx108556-9Z2xWp3J7mK1.png",
        "source_url": "https://mangadex.org/title/623d6a9a-efb6-455b-81d3-3532c524b047",
    },
    {
        "external_id": "mangadex-dandadan-latest",
        "title_en": "DAN DA DAN: Occult Spirits, Aliens & Turbo Granny Chronicles",
        "title_hi": "डैन डा डैन: मोमो और ओकारुन की अलौकिक रोमांचक यात्रा",
        "summary_en": "Yukinobu Tatsu's wildly inventive paranormal manga follows spirit-believer Momo Ayase and alien-enthusiast Ken 'Okarun' Takakura. Blending high-octane battle choreography, folklore curses, sci-fi invasions, and touching adolescent romance, Dandadan is an international sensation.",
        "summary_hi": "युकिनोबू तात्सु का अद्भुत पैरानॉर्मल मंगा 'डैन डा डैन' भूत-प्रेतों में विश्वास रखने वाली मोमो अयासे और एलियंस के दीवाने ओकारुन की कहानी है। अलौकिक श्रापों, एलियन हमलों और दिल छू लेने वाले रोमांस का यह मिश्रण पाठकों को हर हफ़्ते मंत्रमुग्ध कर रहा है।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx132029-7P2m3p9K7LmN.png",
        "source_url": "https://mangadex.org/title/12d59d16-6467-4a6c-941d-d421255e2e1e",
    },
    {
        "external_id": "mangadex-solo-leveling-ragnarok",
        "title_en": "Solo Leveling: Ragnarok - Sung Su-ho & The Shadow Sovereign Legacy",
        "title_hi": "सोलो लेवलिंग: रैगनारॉक - सुंग जिन-वू के बेटे की नई गाथा",
        "summary_en": "The sequel webtoon to Chugong's global mega-hit Solo Leveling. Sung Su-ho, son of Shadow Monarch Sung Jin-woo, unseals his inherited cosmic lineage to defend the universe from outer planar gods and ascending dimensional dungeon rifts.",
        "summary_hi": "दुनिया के सबसे लोकप्रिय वेबटून 'सोलो लेवलिंग' की आधिकारिक अगली कड़ी 'रैगनारॉक'। शैडो मोनार्क सुंग जिन-वू के बेटे सुंग सु-हो की छिपी हुई असीम शक्तियों का जागरण और ब्राह्मांडीय खतरों से पृथ्वी की रक्षा की यह नई महागाथा पाठकों के बीच तहलका मचा रही है।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx105398-b673297a7e10.png",
        "source_url": "https://mangadex.org/title/32d76d19-8a05-4db0-9fc2-e0b0648fe9d0",
    },
    {
        "external_id": "mangadex-sakamoto-days-latest",
        "title_en": "SAKAMOTO DAYS: The Legendary Hitman & JCC Order War",
        "title_hi": "साकामोतो डेज़: जेसीसी आर्क और ऑर्डर का खूनी संघर्ष",
        "summary_en": "Yuto Suzuki's action-packed comedy manga details Taro Sakamoto, once the greatest assassin in the criminal underworld, now running a quiet convenience store for his family. Defending his peace requires outwitting rival assassins and corrupt syndicate syndicates.",
        "summary_hi": "युतो सुजुकी का एक्शन और कॉमेडी से भरपूर मंगा 'साकामोतो डेज़'। अंडरवर्ल्ड का सबसे खतरनाक रिटायर्ड हिटमैन तारो साकामोतो अब अपने परिवार के लिए एक छोटी सी किराने की दुकान चलाता है, लेकिन शांति से जीने के लिए उसे दुनिया के सबसे खूंखार हत्यारों को धूल चटानी पड़ती है।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx125828-5Y8c2n7P9L0m.png",
        "source_url": "https://mangadex.org/title/b545d19a-3240-424a-b501-c30985c5b4e7",
    },
    {
        "external_id": "mangadex-blue-lock-latest",
        "title_en": "Blue Lock: Neo Egoist League & The World's Number One Striker",
        "title_hi": "ब्लू लॉक: इसागी योइची और बैस्टर्ड म्यूनिख का निर्णायक मैच",
        "summary_en": "Muneyuki Kaneshiro and Yusuke Nomura's high-stakes football manga continues through the Neo Egoist League. Yoichi Isagi unleashes Metavision against Europe's prodigies to secure his valuation and claim the solitary throne of the ultimate egoist striker.",
        "summary_hi": "मुनेयुकी कानेशीरो का ब्लॉकबस्टर स्पोर्ट्स मंगा 'ब्लू लॉक'। नियो इगोइस्ट लीग में इसागी योइची यूरोप के शीर्ष युवा फुटबॉलर्स के खिलाफ अपनी मेटाविजन तकनीक का इस्तेमाल करके दुनिया का नंबर 1 स्ट्राइकर बनने की होड़ में सबसे आगे बढ़ रहा है।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx106130-9L2x7M4nP5qR.png",
        "source_url": "https://mangadex.org/title/8f2f4c9a-d790-410a-b530-5b5832a8dd93",
    },
]

# -------------------------------------------------------------
# 2. ANIME AWARDS
# -------------------------------------------------------------
AWARD_ITEMS = [
    {
        "external_id": "award-crunchyroll-2024-aoty",
        "title_en": "Crunchyroll Anime Awards 2024: JUJUTSU KAISEN Season 2 Crowned Anime of the Year",
        "title_hi": "क्रंचीरोल एनीमे अवॉर्ड्स 2024: जुजुत्सु काइसेन सीज़न 2 बना वर्ष का सर्वश्रेष्ठ एनीमे",
        "summary_en": "At the 8th annual Crunchyroll Anime Awards held in Tokyo, Japan, JUJUTSU KAISEN Season 2 swept major accolades, officially securing the prestigious Anime of the Year title alongside Best Director and Best Cinematography. Global fan votes and industry jury recognized its historic Shibuya Incident animation.",
        "summary_hi": "टोक्यो में आयोजित 8वें वार्षिक क्रंचीरोल एनीमे अवॉर्ड्स में 'जुजुत्सु काइसेन सीज़न 2' ने इतिहास रचते हुए 'एनीमे ऑफ द ईयर' (Anime of the Year) का सर्वोच्च खिताब अपने नाम किया। इसके अलावा सर्वश्रेष्ठ निर्देशक और सर्वश्रेष्ठ सिनेमैटोग्राफी का सम्मान भी इसी सीरीज़ को मिला।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx145064-7D4Kj7c7kYx9.png",
        "source_url": "https://www.crunchyroll.com/animeawards",
    },
    {
        "external_id": "award-crunchyroll-2024-animation",
        "title_en": "Crunchyroll Anime Awards 2024: Demon Slayer Wins Best Animation",
        "title_hi": "डेमन स्लेयर स्वॉर्डस्मिथ विलेज आर्क: सर्वश्रेष्ठ एनिमेशन और विजुअल्स का खिताब",
        "summary_en": "Studio Ufotable achieved another milestone as Demon Slayer: Kimetsu no Yaiba Swordsmith Village Arc won Best Animation and Best Art Direction at the global Crunchyroll Anime Awards. The fiery battles between the Mist and Love Hashira against Upper Rank demons set global production benchmarks.",
        "summary_hi": "स्टूडियो यूफ़ोटेबुल (Ufotable) ने एक बार फिर साबित किया कि विजुअल्स और एनिमेशन में उनका कोई मुकाबला नहीं है। 'डेमन स्लेयर: स्वॉर्डस्मिथ विलेज आर्क' ने दुनिया भर के करोड़ों फैंस के वोटों के आधार पर 'बेस्ट एनिमेशन' और 'बेस्ट आर्ट डायरेक्शन' का ग्लोबल अवॉर्ड जीता।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx145139-4K7Y6p9LkM1a.png",
        "source_url": "https://www.crunchyroll.com/animeawards",
    },
    {
        "external_id": "award-crunchyroll-2024-continuing",
        "title_en": "Crunchyroll Anime Awards 2024: One Piece Named Best Continuing Series",
        "title_hi": "वन पीस (One Piece): 25 वर्षों बाद भी बना दुनिया की सर्वश्रेष्ठ कंटीन्यूइंग सीरीज़",
        "summary_en": "Celebrating over 25 continuous broadcast years, Toei Animation's One Piece captured Best Continuing Series honors at the Crunchyroll Anime Awards. The milestone introduction of Luffy's Gear 5 animation shattered streaming servers and united generations of worldwide manga and anime enthusiasts.",
        "summary_hi": "25 वर्षों से लगातार प्रसारित हो रहे 'वन पीस' ने साबित कर दिया कि कहानी और इमोशन्स में वह आज भी दुनिया का बेताज बादशाह है। लफी के 'गियर 5' ट्रांसफॉर्मेशन के ऐतिहासिक एपिसोड्स की बदौलत इसे 2024 में 'सर्वश्रेष्ठ कंटीन्यूइंग सीरीज़' का अंतरराष्ट्रीय पुरस्कार दिया गया।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx21-YCDanmI44vTx.png",
        "source_url": "https://www.crunchyroll.com/animeawards",
    },
    {
        "external_id": "award-crunchyroll-2025-aoty",
        "title_en": "Crunchyroll Anime Awards 2025: Frieren & Solo Leveling Lead Nominations and Major Triumphs",
        "title_hi": "फ़्रीरेन (Frieren) और सोलो लेवलिंग: 2025 के सबसे बड़े ग्लोबल अवॉर्ड्स विजेता",
        "summary_en": "The 2025 anime awards season spotlighted Madhouse's fantasy triumph Frieren: Beyond Journey's End alongside A-1 Pictures' supernatural phenomenon Solo Leveling. Both series dominated global streaming metrics and critical ratings, taking home top honors for fantasy storytelling and action choreography.",
        "summary_hi": "वर्ष 2025 के एनीमे अवॉर्ड्स में मैडहाउस की दिल छू लेने वाली फैंटेसी 'फ़्रीरेन: बियॉन्ड जर्नीज़ एंड' और ए-1 पिक्चर्स की धमाकेदार 'सोलो लेवलिंग' ने संयुक्त रूप से अपना दबदबा बनाया। फ़्रीरेन ने सर्वश्रेष्ठ कहानी व म्यूज़िक और सोलो लेवलिंग ने बेस्ट एक्शन सीरीज़ का ताज जीता।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx154587-47D8p2n7P9Lm.png",
        "source_url": "https://www.crunchyroll.com/animeawards",
    },
    {
        "external_id": "award-taaf-2024-ghibli",
        "title_en": "Tokyo Anime Award Festival 2024: Hayao Miyazaki's The Boy and the Heron Wins Film of the Year",
        "title_hi": "द बॉय एंड द हेरॉन (स्टूडियो घिबली): टोक्यो एनीमे अवॉर्ड में हयाओ मियाज़ाकी की ऐतिहासिक जीत",
        "summary_en": "Legendary auteur Hayao Miyazaki and Studio Ghibli secured the grand Animation of the Year prize for The Boy and the Heron at the Tokyo Anime Award Festival (TAAF 2024), preceding their celebrated Academy Award win in Hollywood. The hand-drawn visual storytelling received unanimous jury acclaim.",
        "summary_hi": "महान निर्देशक हयाओ मियाज़ाकी और स्टूडियो घिबली की उत्कृष्ट रचना 'द बॉय एंड द हेरॉन' (The Boy and the Heron) ने टोक्यो एनीमे अवॉर्ड फेस्टिवल में सर्वश्रेष्ठ एनिमेटेड फीचर फिल्म का ग्रैंड प्राइज जीता। हाथ से बने कलात्मक एनिमेशन की इस जीत को वैश्विक सिनेमा में ऐतिहासिक माना गया।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx108465-9N2x7M4nP5qR.png",
        "source_url": "https://animefestival.jp/en/",
    },
    {
        "external_id": "award-taaf-2025-tv",
        "title_en": "Tokyo Anime Award Festival 2025: OSHI NO KO Celebrated for Television Animation Excellence",
        "title_hi": "ओशी नो को (OSHI NO KO): टेलीविज़न कैटेगरी में सर्वश्रेष्ठ एनीमे प्रोडक्शन का पुरस्कार",
        "summary_en": "At TAAF 2025, Studio Doga Kobo's dramatic adaptation of OSHI NO KO Season 2 garnered television industry accolades for thematic depth, stage play choreography, and piercing insights into contemporary Japanese entertainment media.",
        "summary_hi": "टोक्यो एनीमे अवॉर्ड्स 2025 में स्टूडियो डोगा कोबो द्वारा निर्मित 'ओशी नो को सीज़न 2' को टेलीविज़न एनीमेशन में उत्कृष्टता के लिए सम्मानित किया गया। जापानी एंटरटेनमेंट इंडस्ट्री की वास्तविक सच्चाइयों और 2.5D थिएटर प्ले के शानदार चित्रण ने इसे जूरी की पहली पसंद बनाया।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx166531-9L2x7M4nP5qR.png",
        "source_url": "https://animefestival.jp/en/",
    },
]

# -------------------------------------------------------------
# 3. HIGH-QUALITY EDITORIAL ARTICLES
# -------------------------------------------------------------
NEW_ARTICLES = [
    {
        "title": "ड्रैगन बॉल (Dragon Ball) पूरा वॉच ऑर्डर: DB, DBZ, GT और Super हिंदी में कैसे देखें",
        "slug": "dragon-ball-complete-watch-order-hindi-guide",
        "category": "watch-order",
        "category_names": ["watch-order", "action", "beginners-guide"],
        "platforms": ["Crunchyroll", "JioHotstar"],
        "hindi_dub": "yes",
        "is_featured": False,
        "meta_description": "ड्रैगन बॉल कैसे शुरू करें? जानिए ड्रैगन बॉल, ज़ेड, जीटी और सुपर का सही क्रोनोलॉजिकल वॉच ऑर्डर, कैनन फिल्में और भारत में आधिकारिक स्ट्रीमिंग गाइड।",
        "cover_image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx223-G76uX6q2p1K1.png",
        "content_md": """## ड्रैगन बॉल: एनीमे इतिहास की सबसे अमर गाथा

अकीरा तोरियामा द्वारा रचित **ड्रैगन बॉल (Dragon Ball)** केवल एक एनीमे नहीं, बल्कि विश्वभर में शोनेन एनीमे विधा की नींव रखने वाला महाकाव्य है। सोन गोकू (Goku) की एक मासूम बंदर की पूंछ वाले बच्चे से लेकर ब्रह्मांड के देवताओं (Ultra Instinct) को टक्कर देने तक की यात्रा हर पीढ़ी को रोमांचित करती है।

यदि आप पहली बार ड्रैगन बॉल देखने की योजना बना रहे हैं, तो इसके विशाल ब्रह्मांड में अनगिनत सीरीज़, स्पिन-ऑफ्स और मूवीज़ देखकर भ्रमित होना स्वाभाविक है। इस गाइड में हमने आपके लिए एक **सरल, सटीक और समय बचाने वाला वॉच ऑर्डर** तैयार किया है।

---

### 1. अनुशंसित वॉच ऑर्डर (Chronological Canon Watch Order)

यदि आप गोकू की कहानी को आधिकारिक और बिना किसी गैर-जरूरी भटकाव के देखना चाहते हैं, तो इस क्रम का पालन करें:

1. **Dragon Ball (1986):** कुल 153 एपिसोड। यहाँ गोकू, बुलमा, मास्टर रोशी और क्रिलिन की शुरुआत होती है।
2. **Dragon Ball Z (1989):** कुल 291 एपिसोड। सैयान सागा, फ्रीज़ा सागा, सेल सागा और माजिन बू सागा।
   - *सुझाव:* यदि आप फिलर्स छोड़ना चाहते हैं, तो इसकी जगह **Dragon Ball Z Kai (167 एपिसोड)** देख सकते हैं, जो री-मास्टर्ड और हाई-डेफिनिशन है।
3. **Dragon Ball Super (2015):** कुल 131 एपिसोड। बैटल ऑफ गॉड्स से लेकर टूर्नामेंट ऑफ पावर तक।
4. **Dragon Ball Super: Broly (2018 Movie):** कैनन मूवी, जिसमें ब्रोली की आधिकारिक कहानी है।
5. **Dragon Ball Super: Super Hero (2022 Movie):** गोहान और पिकोलो की नई शक्तियों (Beast Gohan & Orange Piccolo) का परिचय।

---

### 2. क्या Dragon Ball GT देखना ज़रूरी है?

**Dragon Ball GT (1996)** अकीरा तोरियामा के मूल मंगा पर आधारित नहीं है। यह तोई एनिमेशन की एक स्वतंत्र सीरीज़ थी, जिसमें **Super Saiyan 4** दिखाया गया था। यद्यपि यह आधिकारिक कैनन टाइमलाइन का हिस्सा नहीं है, फिर भी यदि आप गोकू के पुराने नॉस्टैल्जिया का आनंद लेना चाहते हैं, तो DBZ के बाद इसे देख सकते हैं।

---

### 3. भारत में कहाँ देखें और हिंदी डब की स्थिति

* **Crunchyroll India:** ड्रैगन बॉल ज़ेड और ड्रैगन बॉल सुपर के अधिकांश भाग क्रंचीरोल इंडिया पर आधिकारिक रूप से स्ट्रीम हो रहे हैं।
* **हिंदी डब उपलब्धता:** ड्रैगन बॉल सुपर और मूवीज़ के हिंदी डब एपिसोड्स भारत के प्रमुख टीवी नेटवर्क्स (Cartoon Network India) और ओटीटी प्लेटफॉर्म्स पर प्रसारित किए जा चुके हैं।
""",
    },
    {
        "title": "सर्वकालिक टॉप 10 सर्वश्रेष्ठ एनीमे (Top 10 Anime of All Time): हिंदी में पूरी रैंकिंग और समीक्षा",
        "slug": "top-10-best-anime-of-all-time-hindi-ranking",
        "category": "rankings",
        "category_names": ["rankings", "recommendations", "action"],
        "platforms": ["Netflix", "Crunchyroll"],
        "hindi_dub": "yes",
        "is_featured": True,
        "meta_description": "दुनिया के सबसे बेहतरीन टॉप 10 एनीमे कौन से हैं? जानिए कहानी, एनिमेशन, प्रभाव और भारत में उपलब्धता के आधार पर हमारी आधिकारिक रैंकिंग।",
        "cover_image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx16498-m5luTQtDDUI4.png",
        "content_md": """## दुनिया के 10 सर्वश्रेष्ठ एनीमे: कहानी और कला का चरम

विश्वभर के लाखों प्रशंसकों और समीक्षकों के मूल्यांकन के बाद, कुछ एनीमे ऐसे होते हैं जो समय की कसौटी पर हमेशा खरे उतरते हैं। ये सिर्फ मनोरंजन नहीं हैं, बल्कि इंसानी भावनाओं, दर्शन, त्याग और साहस के अद्भुत दस्तावेज हैं।

यहाँ 'Hindi Anime Guide' की संपादकीय टीम द्वारा तैयार की गई **ऑल-टाइम टॉप 10 एनीमे रैंकिंग** प्रस्तुत है:

---

### 1. फुलमेटल अल्केमिस्ट: ब्रदरहुड (Fullmetal Alchemist: Brotherhood)
- **शैली:** फैंटेसी, एडवेंचर, ड्रामा
- **कहाँ देखें:** Netflix
- **कहानी का सार:** दो भाई एडवर्ड और अल्फोंस एलरिक अपनी मृत माँ को पुनर्जीवित करने के प्रयास में अपना शरीर खो बैठते हैं। यह सीरीज़ 'बराबर विनिमय' (Law of Equivalent Exchange) के दर्शन पर आधारित दुनिया की सबसे संतुलित कहानी है।

---

### 2. अटैक ऑन टाइटन (Attack on Titan - Shingeki no Kyojin)
- **शैली:** डार्क फैंटेसी, मिस्ट्री, एक्शन
- **कहाँ देखें:** Crunchyroll, Netflix
- **कहानी का सार:** दीवारों के पीछे कैद इंसानियत और इंसानों को खाने वाले विशालकाय टाइटन्स। आज़ादी की कीमत, नफरत का चक्रव्यूह और एरेन येगर का क्रांतिकारी किरदार इसे इतिहास की सबसे प्रभावशाली सीरीज़ बनाता है।

---

### 3. डेथ नोट (Death Note)
- **शैली:** साइकोलॉजिकल थ्रिलर, सुपरनैचुरल
- **कहाँ देखें:** Netflix, JioHotstar
- **कहानी का सार:** एक प्रतिभाशाली छात्र लाइट यागामी को एक नोटबुक मिलती है, जिसमें नाम लिखने से व्यक्ति मर जाता है। लाइट और जासूस 'L' के बीच का दिमागी खेल बुद्धि की पराकाष्ठा है।

---

### 4. जुजुत्सु काइसेन (Jujutsu Kaisen)
- **शैली:** सुपरनैचुरल एक्शन
- **कहाँ देखें:** Netflix, Crunchyroll (हिंदी डब उपलब्ध)
- **कहानी का सार:** कर्स और सॉसरर्स की आधुनिक दुनिया। गोत्जो सातोरो की असीम शक्ति और शिबुया इंसिडेंट का ऐतिहासिक एनिमेशन।

---

### 5. डेमन स्लेयर (Demon Slayer: Kimetsu no Yaiba)
- **शैली:** हिस्टोरिकल, सुपरनैचुरल एक्शन
- **कहाँ देखें:** Netflix, Crunchyroll, JioHotstar (हिंदी डब उपलब्ध)
- **कहानी का सार:** तंजीरो कमादो की अपनी दानव बनी बहन नेजुको को इंसान बनाने की दिल छू लेने वाली यात्रा।

---

### 6. फ़्रीरेन: बियॉन्ड जर्नीज़ एंड (Frieren)
- **शैली:** फैंटेसी, स्लाइस ऑफ लाइफ
- **कहाँ देखें:** Crunchyroll, Netflix

### 7. वन पीस (One Piece)
- **शैली:** एडवेंचर, फैंटेसी
- **कहाँ देखें:** Crunchyroll, Netflix

### 8. स्टाइन्स;गेट (Steins;Gate)
- **शैली:** टाइम ट्रेवल, साइंस फिक्शन
- **कहाँ देखें:** Crunchyroll

### 9. हंटर x हंटर (Hunter x Hunter 2011)
- **शैली:** एडवेंचर, शोनेन
- **कहाँ देखें:** Netflix

### 10. मॉन्स्टर (Monster)
- **शैली:** साइकोलॉजिकल मर्डर मिस्ट्री
- **कहाँ देखें:** Netflix
""",
    },
    {
        "title": "अगर आपको डेथ नोट पसंद आया तो ये 5 माइंड-बेंडिंग थ्रिलर एनीमे ज़रूर देखें",
        "slug": "5-mind-bending-anime-like-death-note-hindi",
        "category": "recommendations",
        "category_names": ["recommendations", "thriller"],
        "platforms": ["Netflix", "Crunchyroll"],
        "hindi_dub": "no",
        "is_featured": False,
        "meta_description": "डेथ नोट जैसी गहरी दिमागी लड़ाई और ट्विस्ट पसंद हैं? कोड गीआस, मॉन्स्टर और साइको-पास जैसे 5 सर्वश्रेष्ठ साइकोलॉजिकल थ्रिलर एनीमे की पूरी सूची।",
        "cover_image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx1575-ReA30sL97cM9.png",
        "content_md": """## डेथ नोट के बाद क्या देखें? माइंड-गेम्स और रहस्य के 5 मास्टरपीस

**डेथ नोट (Death Note)** देखने के बाद दर्शकों की एक सामान्य समस्या होती है — कोई भी दूसरा एनीमे आसानी से पसंद नहीं आता! लाइट यागामी और एल के बीच का शतरंज का मुकाबला, हर मोड़ पर नए ट्विस्ट और न्याय की जटिल परिभाषा इतनी प्रभावशाली है कि इसके समान रोमांच पाना कठिन लगता है।

यदि आप उसी स्तर का मानसिक द्वंद्व और रोमांच खोज रहे हैं, तो ये 5 एनीमे आपकी अगली वॉचलिस्ट में होने ही चाहिए:

---

### 1. कोड गीआस: लेलोच ऑफ द रिबेलियन (Code Geass)
* **समानता:** लेलोच लैम्परेज बिल्कुल लाइट यागामी जैसा天才 रणनीतिकार है। उसे किसी को भी अपनी आज्ञा मानने के लिए विवश करने वाली शक्ति (Geass) मिलती है।
* **क्यों देखें:** राजनीति, शतरंज जैसी युद्ध नीतियाँ और एनीमे इतिहास का सबसे प्रसिद्ध अंतिम क्लाइमेक्स।
* **कहाँ देखें:** Crunchyroll, Netflix

---

### 2. मॉन्स्टर (Monster - नाओकी उरासावा)
* **समानता:** वास्तविक दुनिया पर आधारित शुद्ध साइकोलॉजिकल थ्रिलर। कोई सुपरनैचुरल शक्ति नहीं, केवल इंसानी फितरत और बुराई का अध्ययन।
* **क्यों देखें:** डॉ. केंज़ो तेन्मा और इतिहास के सबसे भयानक विलेन जोहान लीबर्ट की कहानी।
* **कहाँ देखें:** Netflix

---

### 3. साइको-पास (Psycho-Pass)
* **समानता:** एक ऐसी भविष्यवादी दुनिया जहाँ कंप्यूटर सिस्टम (सिबिल सिस्टम) तय करता है कि कोई व्यक्ति अपराध करने वाला है या नहीं।
* **क्यों देखें:** समाज, कानून और स्वतंत्र सोच पर तीखे सवाल खड़े करने वाला साइबरपंक क्राइम थ्रिलर।
* **कहाँ देखें:** Crunchyroll

---

### 4. द प्रॉमिस्ड नेवरलैंड (The Promised Neverland - Season 1)
* **समानता:** अनाथालय में पलने वाले 3 जीनियस बच्चे और उनकी देखभाल करने वाली 'माँ' के बीच का खामोश दिमागी युद्ध।
* **क्यों देखें:** सीज़न 1 का हर एपिसोड सांसें रोक देने वाले सस्पेंस से भरा है।
* **कहाँ देखें:** Netflix

---

### 5. एर्गो प्रॉक्सी (Ergo Proxy)
* **समानता:** दार्शनिक रहस्य और अस्तित्व का सवाल।
* **क्यों देखें:** गहरे बौद्धिक विचार और अनोखा डार्क नोयर वातावरण।
""",
    },
    {
        "title": "योर नेम (Your Name) और वेदरिंग विद यू: मकोतो शिंकाई की जादुई रोमांटिक दुनिया कहाँ देखें",
        "slug": "your-name-makoto-shinkai-romance-anime-hindi",
        "category": "romance",
        "category_names": ["romance", "recommendations"],
        "platforms": ["Netflix", "JioHotstar"],
        "hindi_dub": "yes",
        "is_featured": False,
        "meta_description": "मकोतो शिंकाई की खूबसूरत फिल्में: योर नेम (Kimi no Na wa), वेदरिंग विद यू और सुजुमे भारत में कहाँ देखें। कहानी, समीक्षा और हिंदी ऑडियो उपलब्धता।",
        "cover_image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx21519-XIr3P9k7L1mN.png",
        "content_md": """## मकोतो शिंकाई: बादलों, बारिश और बिछड़े दिलों के जादूगर

जब भी जापानी एनिमेशन में विहंगम प्राकृतिक दृश्यों, तारों से भरे आसमान और दिल को झकझोर देने वाले प्रेम की बात होती है, तो **मकोतो शिंकाई (Makoto Shinkai)** का नाम सबसे पहले आता है। उनकी फिल्मों ने दुनिया भर में एनीमे को एक नई वैश्विक पहचान दिलाई है।

---

### 1. योर नेम (Your Name - Kimi no Na wa)
* **कहानी:** टोक्यो में रहने वाला लड़का ताकी और ग्रामीण इलाके की लड़की मित्सुहा सपनों में एक-दूसरे के शरीर में प्रवेश करने लगते हैं। जब एक धूमकेतु उनके जीवन को बदल देता है, तो वे समय और दूरी के पार एक-दूसरे को खोजने की कोशिश करते हैं।
* **विजुअल्स और संगीत:** RADWIMPS का बैकग्राउंड स्कोर और हर फ्रेम को वॉलपेपर की तरह तराशा गया आर्टवर्क।
* **कहाँ देखें:** Netflix India, Prime Video (रेंटल उपलब्ध)

---

### 2. वेदरिंग विद यू (Weathering With You - Tenki no Ko)
* **कहानी:** लगातार बारिश से जूझते टोक्यो में भागा हुआ लड़का होदाका एक ऐसी लड़की 'हिना' से मिलता है, जो प्रार्थना करके आसमान को साफ कर सकती है। लेकिन कुदरत के नियमों से खिलवाड़ की भारी कीमत चुकानी पड़ती है।
* **कहाँ देखें:** Netflix India, JioHotstar (हिंदी डब उपलब्ध)

---

### 3. सुजुमे (Suzume no Tojimari - 2022)
* **कहानी:** 17 वर्षीय सुजुमे और एक रहस्यमय युवक सोता पूरे जापान में आपदाओं के द्वार बंद करने की यात्रा पर निकलते हैं।
* **कहाँ देखें:** Netflix India

---

### भारतीय दर्शकों के लिए विशेष संदेश
'वेदरिंग विद यू' पहली जापानी एनीमे फिल्म थी, जिसे भारतीय फैंस की याचिका (Petition) के बाद भारत के सिनेमाघरों में बड़े पैमाने पर रिलीज़ किया गया था। यदि आपने अभी तक मकोतो शिंकाई की फिल्में नहीं देखी हैं, तो 'योर नेम' से शुरुआत करना एक अविस्मरणीय अनुभव होगा।
""",
    },
    {
        "title": "हाइक्यू!! (Haikyuu!!) और ब्लू लॉक: खेल, जुनून और टीम वर्क के दो सबसे बेहतरीन स्पोर्ट्स एनीमे",
        "slug": "haikyuu-blue-lock-sports-anime-hindi-guide",
        "category": "sports",
        "category_names": ["sports", "action", "recommendations"],
        "platforms": ["Netflix", "Crunchyroll"],
        "hindi_dub": "yes",
        "is_featured": False,
        "meta_description": "वॉलीबॉल का इमोशनल टीम वर्क या फुटबॉल का खूंखार इगो? हाइक्यू और ब्लू लॉक की तुलना, कहानी और भारत में देखने की पूरी गाइड।",
        "cover_image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx20464-9M4x7p9K1LmN.png",
        "content_md": """## स्पोर्ट्स एनीमे: जहाँ हर सेकंड में रोंगटे खड़े होते हैं

स्पोर्ट्स एनीमे की सबसे बड़ी खासियत यह है कि भले ही आपने वास्तविक जीवन में वह खेल कभी न खेला हो, लेकिन स्क्रीन पर पात्रों का पसीना, उनकी मेहनत और आखिरी सेकंड का थ्रिल आपको अपनी सीट से उछलने पर मजबूर कर देता है।

आधुनिक स्पोर्ट्स एनीमे में दो सबसे बड़े नाम हैं — **हाइक्यू!! (Haikyuu!!)** और **ब्लू लॉक (Blue Lock)**। दोनों खेल पर आधारित हैं, लेकिन दोनों का दर्शन एक-दूसरे से बिल्कुल विपरीत है!

---

### 1. हाइक्यू!! (Haikyuu!!) - टीम वर्क और सपनों की उड़ान
* **खेल:** वॉलीबॉल (Volleyball)
* **मुख्य पात्र:** शोयो हिनाता (कम कद का अदम्य जज़्बे वाला लड़का) और तोबियो कागेयामा (प्रतिभाशाली सेटर)।
* **दर्शन:** "छह लोग मिलकर एक मजबूत टीम बनाते हैं।" यहाँ हर खिलाड़ी की कमजोरी और ताकत मिलकर कोर्ट पर जीत दिलाती है।
* **कहाँ देखें:** Netflix India, Crunchyroll

---

### 2. ब्लू लॉक (Blue Lock) - शुद्ध अहंकार और नंबर 1 स्ट्राइकर
* **खेल:** फुटबॉल (Football)
* **मुख्य पात्र:** इसागी योइची
* **दर्शन:** "टीम वर्क भूल जाओ, दुनिया का सबसे बड़ा स्वार्थी और आक्रामक स्ट्राइकर बनो।" 300 युवा स्ट्राइकर्स को एक जेल जैसी सुविधा में बंद कर दिया जाता है, जहाँ हारने वाले का करियर हमेशा के लिए खत्म हो जाता है।
* **कहाँ देखें:** Netflix, Crunchyroll (हिंदी ऑडियो में भी उपलब्ध)

---

### निष्कर्ष: पहले कौन सा देखें?
यदि आप भावनात्मक जुड़ाव, दोस्ती और प्रेरणादायक यात्रा चाहते हैं, तो **हाइक्यू!!** देखें।  
यदि आपको तेज़-तर्रार मुकाबला, माइंड-गेम्स और रोंगटे खड़े कर देने वाला सस्पेंस पसंद है, तो **ब्लू लॉक** तुरंत शुरू करें!
""",
    },
]


def run_master_seeding() -> None:
    logger.info("Starting master content seeding into Neon PostgreSQL...")
    with Session(engine) as session:
        # 1. Ingest Manga items
        logger.info("Ingesting %d Manga updates...", len(MANGA_ITEMS))
        manga_added = 0
        for m in MANGA_ITEMS:
            existing = session.exec(select(AnimeDraft).where(AnimeDraft.external_id == m["external_id"])).first()
            if not existing:
                draft = AnimeDraft(
                    content_type="manga",
                    status="published",
                    source_name="MangaDex",
                    source_url=m["source_url"],
                    external_id=m["external_id"],
                    title_en=m["title_en"],
                    title_hi=m["title_hi"],
                    summary_en=m["summary_en"],
                    summary_hi=m["summary_hi"],
                    image_url=m["image_url"],
                    published_at=NOW,
                )
                session.add(draft)
                manga_added += 1
            else:
                existing.status = "published"
                existing.published_at = existing.published_at or NOW
                session.add(existing)

        # 2. Ingest Award items
        logger.info("Ingesting %d Anime Awards...", len(AWARD_ITEMS))
        award_added = 0
        for a in AWARD_ITEMS:
            existing = session.exec(select(AnimeDraft).where(AnimeDraft.external_id == a["external_id"])).first()
            if not existing:
                draft = AnimeDraft(
                    content_type="award",
                    status="published",
                    source_name="Crunchyroll / TAAF Official",
                    source_url=a["source_url"],
                    external_id=a["external_id"],
                    title_en=a["title_en"],
                    title_hi=a["title_hi"],
                    summary_en=a["summary_en"],
                    summary_hi=a["summary_hi"],
                    image_url=a["image_url"],
                    published_at=NOW,
                )
                session.add(draft)
                award_added += 1
            else:
                existing.status = "published"
                existing.published_at = existing.published_at or NOW
                session.add(existing)

        # 3. Ingest Editorial Articles
        logger.info("Ingesting %d Editorial Articles across categories...", len(NEW_ARTICLES))
        articles_added = 0
        for art_data in NEW_ARTICLES:
            existing = session.exec(select(Article).where(Article.slug == art_data["slug"])).first()
            if not existing:
                article = Article(
                    title=art_data["title"],
                    slug=art_data["slug"],
                    category=art_data["category"],
                    content_md=art_data["content_md"].strip(),
                    meta_description=art_data["meta_description"],
                    cover_image_url=art_data["cover_image_url"],
                    is_featured=art_data["is_featured"],
                    reading_time_minutes=6,
                    platform_links_count=len(art_data["platforms"]),
                    created_at=NOW,
                    updated_at=NOW,
                    published_at=NOW,
                    last_checked=NOW,
                )
                session.add(article)
                session.flush()

                # Links
                for c_name in art_data["category_names"]:
                    session.add(ArticleCategoryLink(article_id=article.id, category_name=c_name))

                for p_id in art_data["platforms"]:
                    session.add(Availability(
                        article_id=article.id,
                        platform_id=p_id,
                        hindi_dub=art_data["hindi_dub"],
                        languages="Hindi, Japanese, English" if art_data["hindi_dub"] == "yes" else "Japanese, English",
                    ))
                articles_added += 1
            else:
                existing.published_at = existing.published_at or NOW
                session.add(existing)

        session.commit()
        logger.info(
            "Master seeding completed successfully! Manga added: %d, Awards added: %d, Articles added: %d",
            manga_added, award_added, articles_added
        )

    invalidate_public_caches()
    logger.info("Public caches invalidated. All updates are live!")


if __name__ == "__main__":
    run_master_seeding()

