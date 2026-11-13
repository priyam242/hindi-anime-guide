"""
Demo Seeder for Hindi Anime Guide (5 Public Editorial Sections)
Populates high-quality published items across:
1. News (एनीमे समाचार)
2. Schedule (सीज़न शेड्यूल IST)
3. Manga (मंगा अपडेट्स)
4. Awards (एनीमे पुरस्कार 2025/2026)
5. Trailers (यूट्यूब ट्रेलर्स व पीवी)

Usage:
  python seed_demo.py          # Seed all 5 sections with status=published
  python seed_demo.py --remove # Cleanly remove all seeded demo records
"""
import sys
from datetime import datetime, timezone, timedelta
from sqlmodel import select

# Ensure project root is in sys.path
from app.db import get_session
from app.models import AnimeDraft

DEMO_PREFIX = "demo_update_"

DEMO_ITEMS = [
    # ---------------- 1. NEWS ----------------
    {
        "content_type": "news",
        "status": "published",
        "source_name": "Crunchyroll News",
        "source_url": "https://www.crunchyroll.com/news",
        "external_id": f"{DEMO_PREFIX}news_solo_leveling_s2",
        "title_en": "Solo Leveling Season 2 'Arise from the Shadow' Confirmed for Global Streaming",
        "title_hi": "सोलो लेवलिंग सीज़न 2 'अराइज़ फ्रॉम द शैडो' की वैश्विक स्ट्रीमिंग तिथि की आधिकारिक पुष्टि",
        "summary_en": "Aniplex and A-1 Pictures have officially revealed the premiere details for Solo Leveling Season 2. Sung Jinwoo returns with new shadow monarchs and high-stakes dungeon battles.",
        "summary_hi": "एनिप्लेक्स और ए-1 पिक्चर्स ने सोलो लेवलिंग सीज़न 2 के प्रसारण विवरण की आधिकारिक पुष्टि कर दी है। सुंग जिन-वू नई शैडो सेना और विशाल डंजन लड़ाइयों के साथ वापसी कर रहे हैं।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx151807-m1g5ha119SN2.jpg",
        "published_at": datetime.now(timezone.utc) - timedelta(hours=3),
    },
    {
        "content_type": "news",
        "status": "published",
        "source_name": "Anime News Network",
        "source_url": "https://www.animenewsnetwork.com",
        "external_id": f"{DEMO_PREFIX}news_demon_slayer_movie",
        "title_en": "Demon Slayer: Infinity Castle Movie Trilogy Scheduled for Worldwide Theatrical Release",
        "title_hi": "डेमन स्लेयर: इन्फिनिटी कासल मूवी ट्रिलॉजी का वैश्विक सिनेमाघरों में भव्य प्रदर्शन तय",
        "summary_en": "Ufotable confirms the final arc of Kimetsu no Yaiba will premiere as a three-part cinematic experience distributed globally by Sony Pictures and Crunchyroll.",
        "summary_hi": "यूफोटेबुल ने पुष्टि की है कि किमेत्सु नो याइबा का अंतिम आर्क तीन भागों की सिनेमाई फ़िल्म श्रृंखला के रूप में दुनिया भर के थिएटर्स में रिलीज़ होगा।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx145139-c5cI7W5N414M.jpg",
        "published_at": datetime.now(timezone.utc) - timedelta(hours=6),
    },
    {
        "content_type": "news",
        "status": "published",
        "source_name": "Weekly Shonen Jump",
        "source_url": "https://www.shonenjump.com",
        "external_id": f"{DEMO_PREFIX}news_jujutsu_kaisen_culling",
        "title_en": "Jujutsu Kaisen: Culling Game Arc Enters Production at Studio MAPPA",
        "title_hi": "जुजुत्सु काइसेन: कलिंग गेम आर्क का निर्माण स्टूडियो मप्पा में आधिकारिक रूप से शुरू",
        "summary_en": "Following the Shibuya Incident, Studio MAPPA has announced that the intense Culling Game arc is currently in full production with director Shota Goshozono returning.",
        "summary_hi": "शिबुया इंसिडेंट के बाद स्टूडियो मप्पा ने घोषणा की है कि कलिंग गेम आर्क का निर्माण शुरू हो चुका है और निर्देशक शोटा गोशोज़ोनो प्रोजेक्ट का नेतृत्व करेंगे।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx113415-bbBWj4pEFseh.jpg",
        "published_at": datetime.now(timezone.utc) - timedelta(hours=14),
    },
    {
        "content_type": "news",
        "status": "published",
        "source_name": "IGN Anime",
        "source_url": "https://www.ign.com/anime",
        "external_id": f"{DEMO_PREFIX}news_chainsaw_man_reze",
        "title_en": "Chainsaw Man: The Movie - Reze Arc Teaser Stuns Fans with First Look",
        "title_hi": "चेनसॉ मैन: द मूवी - रेज़ आर्क के पहले टीज़र ने प्रशंसकों को किया रोमांचित",
        "summary_en": "Denji's romance with the mysterious Reze will be told in a feature film format, expanding the dark fantasy universe of Tatsuki Fujimoto.",
        "summary_hi": "डेंजी और रहस्यमयी रेज़ की प्रेम कहानी फ़ीचर फ़िल्म प्रारूप में प्रस्तुत की जाएगी, जो तात्सुकी फुजीमोटो की डार्क फैंटेसी दुनिया को आगे बढ़ाएगी।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx127230-FloCoXScQjHT.png",
        "published_at": datetime.now(timezone.utc) - timedelta(days=1),
    },

    # ---------------- 2. SCHEDULE ----------------
    {
        "content_type": "schedule",
        "status": "published",
        "source_name": "AniList Airing",
        "source_url": "https://anilist.co/anime/151807",
        "external_id": f"{DEMO_PREFIX}sched_solo_leveling_ep13",
        "title_en": "Solo Leveling Episode 13",
        "title_hi": "सोलो लेवलिंग (Solo Leveling) - एपिसोड 13",
        "summary_en": "Airing on Saturday at 2026-11-15 18:30 UTC. Sung Jinwoo battles new A-rank gate threats.",
        "summary_hi": "भारतीय समयानुसार रात 10:00 बजे IST। सुंग जिन-वू अपने शैडो सैनिकों के साथ नए ए-रैंक गेट का सामना करेंगे।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx151807-m1g5ha119SN2.jpg",
        "published_at": datetime.now(timezone.utc) + timedelta(days=2),
    },
    {
        "content_type": "schedule",
        "status": "published",
        "source_name": "AniList Airing",
        "source_url": "https://anilist.co/anime/145139",
        "external_id": f"{DEMO_PREFIX}sched_demon_slayer_ep12",
        "title_en": "Demon Slayer: Hashira Training Arc Episode 8",
        "title_hi": "डेमन स्लेयर: हाशिरा ट्रेनिंग आर्क - एपिसोड 8",
        "summary_en": "Airing on Sunday at 2026-11-16 17:45 UTC. The Hashira gather to confront Muzan Kibutsuji.",
        "summary_hi": "भारतीय समयानुसार रात 11:15 बजे IST। सभी हाशिरा मुज़ान किबुत्सुजी के खिलाफ निर्णायक युद्ध के लिए एकजुट होते हैं।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx145139-c5cI7W5N414M.jpg",
        "published_at": datetime.now(timezone.utc) + timedelta(days=3),
    },
    {
        "content_type": "schedule",
        "status": "published",
        "source_name": "AniList Airing",
        "source_url": "https://anilist.co/anime/113415",
        "external_id": f"{DEMO_PREFIX}sched_jujutsu_kaisen_ep48",
        "title_en": "Jujutsu Kaisen Season 2 Episode 24",
        "title_hi": "जुजुत्सु काइसेन सीज़न 2 - एपिसोड 24",
        "summary_en": "Airing on Thursday at 2026-11-20 15:30 UTC. Special battle against cursed spirits.",
        "summary_hi": "भारतीय समयानुसार रात 09:00 बजे IST। युजी इतादोरी और मेगुमी फुशिगुरो का शापित आत्माओं के विरुद्ध मुकाबला।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx113415-bbBWj4pEFseh.jpg",
        "published_at": datetime.now(timezone.utc) + timedelta(days=5),
    },

    # ---------------- 3. MANGA ----------------
    {
        "content_type": "manga",
        "status": "published",
        "source_name": "MangaPlus by Shueisha",
        "source_url": "https://mangaplus.shueisha.co.jp",
        "external_id": f"{DEMO_PREFIX}manga_one_piece_1130",
        "title_en": "One Piece Chapter 1130: The Cursed Prince",
        "title_hi": "वन पीस (One Piece) - चैप्टर 1130: द कर्सड प्रिंस",
        "summary_en": "Luffy and the Straw Hats enter the legendary island of giants, Elbaf, uncovering deep secrets of the ancient past.",
        "summary_hi": "लफी और स्ट्रॉ हैट पाइरेट्स एलबाफ के विशालकाय द्वीप में प्रवेश करते हैं और प्राचीन रहस्यों की खोज करते हैं।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx21-YCDoj1EkAxFn.jpg",
        "published_at": datetime.now(timezone.utc) - timedelta(hours=4),
    },
    {
        "content_type": "manga",
        "status": "published",
        "source_name": "MangaPlus by Shueisha",
        "source_url": "https://mangaplus.shueisha.co.jp",
        "external_id": f"{DEMO_PREFIX}manga_sakamoto_days_186",
        "title_en": "Sakamoto Days Chapter 186: Order of Assassins",
        "title_hi": "साकामोतो डेज़ (Sakamoto Days) - चैप्टर 186",
        "summary_en": "Taro Sakamoto executes high-octane convenience store combat as rival hitmen converge on Tokyo.",
        "summary_hi": "तारो साकामोतो ने टोक्यो में प्रतिद्वंद्वी हत्यारों के खिलाफ जबर्दस्त एक्शन और कॉम्बैट रणनीति दिखाई।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx16498-85e836vOec1n.jpg",
        "published_at": datetime.now(timezone.utc) - timedelta(days=1),
    },
    {
        "content_type": "manga",
        "status": "published",
        "source_name": "Viz Media",
        "source_url": "https://www.viz.com/shonenjump",
        "external_id": f"{DEMO_PREFIX}manga_chainsaw_man_182",
        "title_en": "Chainsaw Man Chapter 182: The Aging Devil",
        "title_hi": "चेनसॉ मैन (Chainsaw Man) - चैप्टर 182",
        "summary_en": "Denji faces the terrifying concepts of the Aging Devil in Fujimoto's psychological masterpiece.",
        "summary_hi": "डेंजी को फुजीमोटो के मनोवैज्ञानिक कथानक में एजिंग डेविल के भयानक स्वरूप का सामना करना पड़ता है।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx127230-FloCoXScQjHT.png",
        "published_at": datetime.now(timezone.utc) - timedelta(days=3),
    },

    # ---------------- 4. AWARDS ----------------
    {
        "content_type": "award",
        "status": "published",
        "source_name": "Crunchyroll Anime Awards 2026",
        "source_url": "https://www.crunchyroll.com/animeawards",
        "external_id": f"{DEMO_PREFIX}award_anime_of_year_2026",
        "title_en": "Crunchyroll Anime Awards 2026: Solo Leveling Wins Anime of the Year",
        "title_hi": "क्रंचीरोल एनीमे अवार्ड्स 2026: सोलो लेवलिंग ने जीता प्रतिष्ठित 'एनीमे ऑफ़ द ईयर' सम्मान",
        "summary_en": "With millions of fan votes globally, Solo Leveling took home the top honor, alongside Best Action and Best Score awards.",
        "summary_hi": "विश्वभर के लाखों प्रशंसकों के वोटों के साथ सोलो लेवलिंग ने बेस्ट एक्शन और बेस्ट एनीमे ऑफ़ द ईयर का सर्वोच्च पुरस्कार अपने नाम किया।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx151807-m1g5ha119SN2.jpg",
        "published_at": datetime.now(timezone.utc) - timedelta(days=2),
    },
    {
        "content_type": "award",
        "status": "published",
        "source_name": "Tokyo Anime Award Festival",
        "source_url": "https://animefestival.jp/en/",
        "external_id": f"{DEMO_PREFIX}award_best_animation_2026",
        "title_en": "Tokyo Anime Award Festival 2026: Demon Slayer Honored for Best Animation",
        "title_hi": "टोक्यो एनीमे अवार्ड फ़ेस्टिवल 2026: डेमन स्लेयर को सर्वश्रेष्ठ एनीमेशन का पुरस्कार",
        "summary_en": "Studio Ufotable's breathtaking visual effects in the Hashira Training Arc earned top praise from industry judges in Tokyo.",
        "summary_hi": "हाशिरा ट्रेनिंग आर्क में स्टूडियो यूफोटेबुल के अद्भुत विजुअल इफेक्ट्स को टोक्यो में उद्योग जजों द्वारा सर्वोच्च सम्मान प्रदान किया गया।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx145139-c5cI7W5N414M.jpg",
        "published_at": datetime.now(timezone.utc) - timedelta(days=5),
    },

    # ---------------- 5. TRAILERS ----------------
    {
        "content_type": "trailer",
        "status": "published",
        "source_name": "Aniplex Official YouTube",
        "source_url": "https://www.youtube.com/watch?v=s8Y6d2vE-dE",
        "external_id": f"{DEMO_PREFIX}trailer_solo_leveling_s2",
        "title_en": "Solo Leveling Season 2 - Official Main Trailer PV | Aniplex",
        "title_hi": "सोलो लेवलिंग सीज़न 2 - आधिकारिक मुख्य ट्रेलर PV | एनिप्लेक्स",
        "summary_en": "Watch the explosive official trailer for Solo Leveling Season 2 'Arise from the Shadow' featuring SawanoHiroyuki[nZk]'s theme song.",
        "summary_hi": "सोलो लेवलing सीज़न 2 का आधिकारिक ट्रेलर देखें, जिसमें सवानो हिरोयुकी का शानदार बैकग्राउंड स्कोर शामिल है।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx151807-m1g5ha119SN2.jpg",
        "published_at": datetime.now(timezone.utc) - timedelta(hours=10),
    },
    {
        "content_type": "trailer",
        "status": "published",
        "source_name": "TOHO animation YouTube",
        "source_url": "https://www.youtube.com/watch?v=pkZXbh6f554",
        "external_id": f"{DEMO_PREFIX}trailer_jujutsu_kaisen_culling",
        "title_en": "Jujutsu Kaisen: Culling Game Arc Official Teaser Trailer | TOHO",
        "title_hi": "जुजुत्सु काइसेन: कलिंग गेम आर्क आधिकारिक टीज़र ट्रेलर | तोहो एनीमेशन",
        "summary_en": "Official teaser from TOHO animation previewing the high stakes Culling Game deathmatch arc.",
        "summary_hi": "तोहो एनीमेशन द्वारा जारी कलिंग गेम का आधिकारिक टीज़र ट्रेलर जिसमें आगामी डार्क बैटल्स की पहली झलक दिखाई गई है।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx113415-bbBWj4pEFseh.jpg",
        "published_at": datetime.now(timezone.utc) - timedelta(days=1),
    },
    {
        "content_type": "trailer",
        "status": "published",
        "source_name": "MAPPA CHANNEL",
        "source_url": "https://www.youtube.com/watch?v=dflm2uQ5Cks",
        "external_id": f"{DEMO_PREFIX}trailer_chainsaw_man_reze",
        "title_en": "Chainsaw Man: The Movie - Reze Arc Official Teaser PV | MAPPA",
        "title_hi": "चेनसॉ मैन: द मूवी - रेज़ आर्क आधिकारिक टीज़र PV | स्टूडियो मप्पा",
        "summary_en": "Denji and Reze feature in this romantic yet deadly theatrical anime film teaser by Studio MAPPA.",
        "summary_hi": "स्टूडियो मप्पा द्वारा निर्मित चेनसॉ मैन रेज़ आर्क फ़ीचर फ़िल्म का आधिकारिक टीज़र ट्रेलर।",
        "image_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx127230-FloCoXScQjHT.png",
        "published_at": datetime.now(timezone.utc) - timedelta(days=4),
    },
]


def seed_demo_items():
    session = next(get_session())
    created = 0
    updated = 0
    for data in DEMO_ITEMS:
        existing = session.exec(
            select(AnimeDraft).where(AnimeDraft.external_id == data["external_id"])
        ).first()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            session.add(existing)
            updated += 1
        else:
            draft = AnimeDraft(**data)
            session.add(draft)
            created += 1
    session.commit()
    print(f"✅ Demo seeder complete: {created} created, {updated} updated (Total: {len(DEMO_ITEMS)} published items).")


def remove_demo_items():
    session = next(get_session())
    items = session.exec(
        select(AnimeDraft).where(AnimeDraft.external_id.like(f"{DEMO_PREFIX}%"))
    ).all()
    count = len(items)
    for it in items:
        session.delete(it)
    session.commit()
    print(f"🗑️ Successfully removed {count} demo items prefixed with '{DEMO_PREFIX}'.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--remove":
        remove_demo_items()
    else:
        seed_demo_items()
