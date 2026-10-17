"""Text-location resolution with bounded Nominatim lookup and offline fallback."""

import logging
import math
import os
import re
import threading
import time
import json

import requests

try:
    from groq import APIConnectionError, APIStatusError, APITimeoutError, Groq
except ImportError:
    Groq = None
    APIStatusError = ()
    APIConnectionError = ()
    APITimeoutError = ()


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "BMC-Civic-Complaint-Demo/1.0 (local academic project)"
MUMBAI_VIEWBOX = "72.77,19.28,72.99,18.89"
MUMBAI_LAT_RANGE = (18.80, 19.35)
MUMBAI_LNG_RANGE = (72.70, 73.10)
NOMINATIM_RESULT_LIMIT = 5
NOMINATIM_RATE_LIMIT_SECONDS = 1.0

GEOAPIFY_URL = "https://api.geoapify.com/v1/geocode/search"
GEOAPIFY_RESULT_LIMIT = 5
GEOAPIFY_TIMEOUT_SECONDS = 15
GEOAPIFY_RECT = f"rect:{MUMBAI_LNG_RANGE[0]},{MUMBAI_LAT_RANGE[0]},{MUMBAI_LNG_RANGE[1]},{MUMBAI_LAT_RANGE[1]}"

_nominatim_lock = threading.Lock()
_last_nominatim_request_time = 0.0
_nominatim_cache = {}

_geoapify_lock = threading.Lock()
_geoapify_cache = {}
_geoapify_session = requests.Session()

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

WARD_COORDS = {
    "A": (18.938, 72.834), "B": (18.954, 72.836), "C": (18.953, 72.827),
    "D": (18.966, 72.814), "E": (18.978, 72.839), "F/N": (19.023, 72.852),
    "F/S": (19.008, 72.843), "G/N": (19.071, 72.843), "G/S": (19.040, 72.838),
    "H/E": (19.111, 72.856), "H/W": (19.057, 72.834), "K/E": (19.120, 72.873),
    "K/W": (19.125, 72.837), "L": (19.070, 72.886), "M/E": (19.050, 72.915),
    "M/W": (19.030, 72.900), "N": (19.100, 72.920), "P/N": (19.190, 72.850),
    "P/S": (19.150, 72.850), "R/C": (19.235, 72.850), "R/N": (19.265, 72.860),
    "R/S": (19.220, 72.830), "S": (19.160, 72.940), "T": (19.210, 72.960),
}
DEFAULT_MUMBAI = (19.0760, 72.8777)

# Offline hints only. Nominatim is always attempted before ward-centre fallback.
LOCALITY_TO_WARD = [
    ("nariman point", "A"), ("malabar hill", "D"), ("lower parel", "F/S"),
    ("bandra west", "H/W"), ("bandra east", "H/E"), ("andheri east", "K/E"),
    ("andheri west", "K/W"), ("goregaon west", "P/S"), ("goregaon east", "P/N"),
    ("vile parle", "K/E"), ("vidyavihar", "L"), ("vidya vihar", "L"),
    ("jb nagar", "K/E"),
    ("colaba", "A"), ("fort", "A"), ("byculla", "E"), ("mazgaon", "E"),
    ("parel", "F/S"), ("matunga", "F/N"), ("sion", "F/N"), ("wadala", "F/N"),
    ("dadar", "G/N"), ("mahim", "G/N"), ("dharavi", "G/N"), ("worli", "G/S"),
    ("prabhadevi", "G/S"), ("khar", "H/W"), ("bkc", "H/E"),
    ("santacruz", "H/E"), ("seepz", "K/E"), ("versova", "K/W"),
    ("jogeshwari", "K/W"), ("malad", "P/N"), ("kandivali", "R/S"),
    ("borivali", "R/C"), ("dahisar", "R/N"), ("powai", "S"),
    ("vikhroli", "S"), ("ghatkopar", "N"), ("mankhurd", "M/E"),
    ("govandi", "M/E"), ("chembur", "M/W"), ("kurla", "L"),
    ("sakinaka", "L"), ("bhandup", "S"), ("mulund", "T"),
    ("विद्याविहार", "L"), ("घाटकोपर", "N"), ("कुर्ला", "L"), ("चेंबूर", "M/W"),
    # Common Hindi spellings used in citizen reports. Keep these as ward-level
    # fallbacks when a geocoder cannot resolve a more precise landmark.
    ("पवई", "S"), ("भांडुप", "S"), ("मुलुंड", "T"), ("दादर", "G/N"),
    ("माहिम", "G/N"), ("अंधेरी", "K/W"), ("बांद्रा", "H/W"),
]

# Multilingual complaint-boundary words/phrases used to cleanly separate
# location mentions from complaint problem descriptions across EN, HI, MR, and Romanized forms.
COMPLAINT_BOUNDARY_WORDS = [
    # Multi-word phrases first (English, Hindi, Marathi, Romanized)
    r"causing\s+traffic",
    r"traffic\s+jam",
    r"due\s+to",
    r"because\s+of",
    r"there\s+is",
    r"there\s+was",
    r"की\s+वजह\s+से",
    r"वजह\s+से",
    r"के\s+कारण",
    r"होने\s+के\s+कारण",
    r"होने\s+से",
    r"हो\s+रहा",
    r"हो\s+रही",
    r"हो\s+गया",
    r"हो\s+गई",
    r"च्या\s+बाहेर",
    r"च्या\s+समोर",
    r"च्या\s+जवळ",
    r"च्या\s+मागे",
    r"च्या\s+मध्ये",
    r"च्या\s+वर",
    r"च्या\s+मुळे",
    r"झाल्यामुळे",
    r"वाहतूक\s+कोंडी",
    r"मोठा\s+खड्डा",
    r"ki\s+wajah\s+se",
    r"wajah\s+se",
    r"vajah\s+se",
    r"ke\s+karan",
    r"chya\s+mule",
    r"jhalyamule",
    r"vahtuk\s+kondi",
    r"motha\s+khadda",
    r"mothe\s+khadde",
    r"is\s+blocked",
    r"is\s+broken",
    r"is\s+damaged",
    # English single words
    r"is",
    r"was",
    r"are",
    r"were",
    r"has",
    r"have",
    r"had",
    r"having",
    r"causing",
    r"making",
    r"creating",
    r"because",
    r"blocked",
    r"blocking",
    r"broken",
    r"damaged",
    r"overflowing",
    r"flooded",
    r"leaking",
    r"problem",
    r"issue",
    # Hindi single words
    r"है",
    r"हैं",
    r"था",
    r"थी",
    r"थे",
    r"हुआ",
    r"हुई",
    r"हुए",
    r"कारण",
    r"जाम",
    r"ट्रैफिक",
    r"समस्या",
    r"परेशानी",
    r"रुकावट",
    r"दिक्कत",
    r"खराब",
    r"टूटा",
    r"टूटी",
    r"फूटा",
    r"फूटी",
    r"बंद",
    r"कचरा",
    r"गड्ढा",
    # Marathi single words
    r"आहे",
    r"आहेत",
    r"होता",
    r"होती",
    r"होते",
    r"झाला",
    r"झाली",
    r"झाले",
    r"पडला",
    r"पडली",
    r"पडले",
    r"मुळे",
    r"कारणाने",
    r"वाहतूक",
    r"कोंडी",
    r"अडचण",
    r"तुटलेला",
    r"तुटलेली",
    r"फुटलेला",
    r"फुटलेली",
    r"फुटली",
    r"खड्डा",
    r"घाण",
    r"अतिक्रमण",
    # Romanized Hindi/Marathi single words
    r"hai",
    r"hain",
    r"tha",
    r"thi",
    r"the",
    r"aahe",
    r"ahet",
    r"hota",
    r"hoti",
    r"hote",
    r"hua",
    r"hui",
    r"hue",
    r"jhala",
    r"jhali",
    r"jhale",
    r"mule",
    r"karan",
    r"traffic",
    r"jam",
    r"vahtuk",
    r"kondi",
    r"samasya",
    r"pareshani",
    r"adchan",
    r"dikkat",
    r"kharab",
    r"toota",
    r"tuta",
    r"phuta",
    r"futli",
    r"tutlela",
    r"phutlela",
    r"band",
    r"khadda",
    r"khadde",
    r"motha",
    r"mothe",
    r"kachra",
    r"atikraman",
]

BOUNDARY_LOOKAHEAD = (
    r"(?:\s+(?:(?:" + r"|".join(COMPLAINT_BOUNDARY_WORDS) + r")(?=[\s,.;!?]|$))|[,.;!?]|$)"
)

BETWEEN_PATTERNS = [
    # English & Mixed prefix: "between A and/& B [complaint boundary]"
    re.compile(
        r"\bbetween\s+(.+?)\s+(?:and|&)\s+(.+?)(?=" + BOUNDARY_LOOKAHEAD + r")",
        re.IGNORECASE,
    ),
    # Romanized prefix: "between A aur/ani B [complaint boundary]"
    re.compile(
        r"\bbetween\s+(.+?)\s+(?:aur|ani)\s+(.+?)(?=" + BOUNDARY_LOOKAHEAD + r")",
        re.IGNORECASE,
    ),
    # Devanagari prefix: "बीच A और B [complaint boundary]" and "दरम्यान A आणि B [complaint boundary]"
    re.compile(
        r"\bबीच\s+(.+?)\s+(?:और|व)\s+(.+?)(?=" + BOUNDARY_LOOKAHEAD + r")",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bदरम्यान\s+(.+?)\s+(?:आणि|व|ते)\s+(.+?)(?=" + BOUNDARY_LOOKAHEAD + r")",
        re.IGNORECASE,
    ),
    # Romanized prefix: "beech/darmiyan A aur/ani/and B [complaint boundary]"
    re.compile(
        r"\b(?:beech|bich|darmiyan|darmyan)\s+(.+?)\s+(?:aur|ani|and)\s+(.+?)(?=" + BOUNDARY_LOOKAHEAD + r")",
        re.IGNORECASE,
    ),
    # Postfix patterns: "A और/व B के बीच" (B is bounded on the right by के बीच)
    re.compile(
        r"(.+?)\s+(?:और|व)\s+(.+?)\s+(?:के\s+बीच(?:\s+में)?)(?=[,.;!?]|\s+|$)",
        re.IGNORECASE,
    ),
    # Postfix patterns: "A आणि/व/ते B दरम्यान" (B is bounded on the right by दरम्यान)
    re.compile(
        r"(.+?)\s+(?:आणि|व|ते)\s+(.+?)\s+(?:च्या\s+दरम्यान|दरम्यान|च्या\s+मधील|मधील)(?=[,.;!?]|\s+|$)",
        re.IGNORECASE,
    ),
    # Romanized postfix: "A aur/ani B ke beech / darmyan" (B is bounded on the right by ke beech / darmyan)
    re.compile(
        r"\b(.+?)\s+(?:aur|ani)\s+(.+?)\s+(?:ke\s+(?:beech|bich)(?:\s+mein)?|(?:chya\s+)?(?:darmyan|darmiyan|madhye))(?=[,.;!?]|\s+|$)",
        re.IGNORECASE,
    ),
]

POST_BOUNDARY = r"(?=\s|[,.;!?]|$)"

CONNECTOR_PATTERNS = [
    # English prefix: near / outside / at / etc. place followed by complaint boundary
    re.compile(
        r"\b(?:near|outside|at|in|on|opposite|beside|behind|around)\s+(.+?)(?=\s+(?:is|was|are|were|has|have|had|having|causing|making|creating|due\s+to|because|blocked|broken|damaged|overflowing|danger|problem|issue)|[,.;!?]|$)",
        re.IGNORECASE,
    ),
    # Hindi connectors: place followed by के पास / के बाहर / etc.
    re.compile(
        r"(.+?)\s+(?:के\s+(?:पास|बाहर|सामने|पीछे|निकट|नीचे|ऊपर|अंदर)|में|पर)" + POST_BOUNDARY,
        re.IGNORECASE,
    ),
    # Marathi separated connectors: place + (च्या )?जवळ / बाहेर / etc.
    re.compile(
        r"(.+?)(?:\s+(?:च्या\s+)?(?:जवळ|बाहेर|समोर|मागे|मध्ये|वर|खाली|तर्फे))" + POST_BOUNDARY,
        re.IGNORECASE,
    ),
    # Marathi attached connectors: place + च्याबाहेर / च्याजवळ / जवळ / बाहेर / वर / etc.
    re.compile(
        r"(.+?)(?:च्याबाहेर|च्यासमोर|च्याजवळ|च्यामागे|च्यामध्ये|च्यावर|च्याखाली|जवळ|बाहेर|समोर|मागे|मध्ये|वर|खाली)" + POST_BOUNDARY,
        re.IGNORECASE,
    ),
    # Romanized connectors: place + ke paas / chya baher / javal / etc.
    re.compile(
        r"\b(.+?)\s+(?:ke\s+(?:paas|pass|bahar|samne|peeche|piche|niche)|chya\s+(?:baher|samor|javal|mage|madhye|madhe|var|khali)|javal|baher|samor|mage|madhye|madhe)" + POST_BOUNDARY,
        re.IGNORECASE,
    ),
]

PLACE_FEATURE_WORDS = {
    "station", "railway", "metro", "market", "road", "marg", "lane", "nagar",
    "chowk", "bridge", "hospital", "school", "college", "institute", "university",
    "temple", "mosque", "church", "garden", "park", "mall", "depot", "stop",
    "flyover", "highway", "footpath", "circle", "gali", "bazaar", "technology",
    "स्टेशन", "स्थानक", "रोड", "मार्ग", "रस्ता", "चौक", "पूल", "मार्केट", "बाजार",
    "रुग्णालय", "दवाखाना", "शाळा", "कॉलेज", "इन्स्टिट्यूट", "विद्यापीठ", "मंदिर",
    "मशीद", "उद्यान", "बाग", "गल्ली", "नगर", "नाका", "डेपो", "फूटपाथ", "टेक्नोलॉजी", "टेक्नॉलॉजी",
    "रेल्वे", "रेलवे",
    "sthanak", "rasta", "pool", "pul", "dawakana", "shala", "mandir", "masjid",
    "udyan", "bagh", "naka",
}

GENERIC_QUERY_WORDS = {
    # English problem / adjective words
    "pothole", "potholes", "garbage", "waste", "trash", "debris", "water", "pipeline",
    "leakage", "footpath", "footpaths", "blocked", "blocking", "broken", "damaged",
    "large", "huge", "small", "big", "deep", "there", "issue", "issues",
    "problem", "problems", "complaint", "complaints", "traffic", "burst", "overflowing", "danger",
    # Hindi problem / adjective words
    "बड़ा", "बड़ी", "बड़े", "भारी", "गड्ढा", "गड्ढे", "कचरा", "गंदगी", "पानी", "पाइपलाइन",
    "पाइप", "नल", "ट्रैफिक", "जाम", "रुकावट", "समस्या", "दिक्कत", "परेशानी", "खराब", "खतरा",
    "टूटा", "टूटी", "फूटा", "फूटी", "भरा", "भरी", "भरे",
    # Marathi problem / adjective words
    "मोठा", "मोठी", "मोठे", "मोठ्या", "खूप", "प्रचंड", "विशाल", "जास्त",
    "खड्डा", "खड्डे", "खड्ड्यांचा", "खड्ड्यांची", "खड्ड्यांचे", "खड्ड्यात",
    "कचरा", "कचऱ्याचा", "कचऱ्याची", "कचऱ्याचे", "कचऱ्यात", "घाण", "दुर्गंधी",
    "पाणी", "पाण्याचा", "पाण्याची", "पाण्याचे", "पाण्यात",
    "पाईपलाईन", "पाइपलाइन", "पाईप", "पाइप", "नल", "गळती",
    "वाहतूक", "कोंडी", "ट्रॅफिक", "ट्रॅफिकजाम", "अतिक्रमण", "अडथळा", "जाम",
    "समस्या", "अडचण", "अडचणी", "तक्रार", "त्रास", "खराब", "धोका", "धोकादायक",
    "साचला", "साचले", "साचली", "साचलेले", "साचलेली",
    "झाला", "झाली", "झाले", "पडला", "पडली", "पडले",
    "फुटला", "फुटली", "फुटले", "तुटला", "तुटली", "तुटले",
    # Romanized Hindi/Marathi equivalents
    "khadda", "khadde", "kachra", "kachrya", "gaddha", "gaddhe",
    "motha", "mothi", "mothe", "mothya", "bada", "badi", "bade", "bahut",
    "pani", "paani", "pipeline", "traffic", "jam", "vahtuk", "kondi", "samasya",
    "adchan", "dikkat", "pareshani", "kharab", "jhala", "aahe", "hai",
    "unknown", "unnamed",
}

STOP_WORDS = {
    "of", "the", "in", "at", "on", "and", "to", "for", "a", "an", "has", "have", "is", "was",
    "च्या", "का", "के", "की", "व", "ने", "से", "में", "पर", "चा", "ची", "चे", "ऑफ",
    "near", "outside", "opposite", "beside", "behind", "around",
}

FEATURE_CATEGORIES = {
    "worship": {"church", "temple", "mosque", "masjid", "mandir", "derasar", "gurudwara"},
    "education": {"institute", "technology", "college", "school", "university", "vidyapeeth", "shala"},
    "transit": {"station", "railway", "metro", "sthanak", "depot", "terminus"},
    "commerce": {"market", "bazaar", "mall", "supermarket", "bazar"},
    "medical": {"hospital", "clinic", "dispensary", "dawakana", "rugnalaya"},
    "leisure": {"garden", "park", "udyan", "bagh"},
    "road": {"road", "marg", "rasta", "lane", "flyover", "bridge", "street"},
}
ALL_FEATURE_WORDS = set().union(*FEATURE_CATEGORIES.values())
LOCATION_CLASSES = {
    "aeroway", "amenity", "boundary", "building", "highway", "historic", "landuse",
    "leisure", "man_made", "natural", "office", "place", "public_transport", "railway",
    "shop", "tourism",
}

# Canonical Mumbai place spellings used to make multilingual Nominatim
# queries and metadata comparisons accurate and comparable.
CANONICAL_LOCATION_ALIASES = {
    "विद्याविहार": "Vidyavihar",
    "घाटकोपर": "Ghatkopar",
    "कुर्ला": "Kurla",
    "चेंबूर": "Chembur",
    "पवई": "Powai",
    "भांडुप": "Bhandup",
    "मुलुंड": "Mulund",
    "दादर": "Dadar",
    "माहिम": "Mahim",
    "वर्ली": "Worli",
    "अंधेरी": "Andheri",
    "बांद्रा": "Bandra",
    "मालाड": "Malad",
    "railway station": "Railway Station",
    "गोरेगाव": "Goregaon",
    "बोरीवली": "Borivali",
    "कांदिवली": "Kandivali",
    "विखरोली": "Vikhroli",
    "मानखुर्द": "Mankhurd",
    "गोवंडी": "Govandi",
    "साकीनाका": "Sakinaka",
    "नरिमन पॉइंट": "Nariman Point",
    "मलबार हिल": "Malabar Hill",
    "लोअर परळ": "Lower Parel",
    "विलेपार्ले": "Vile Parle",
    "कुलाबा": "Colaba",
    "फोर्ट": "Fort",
    "भायखळा": "Byculla",
    "माझगाव": "Mazgaon",
    "परळ": "Parel",
    "माटुंगा": "Matunga",
    "सायन": "Sion",
    "वडाळा": "Wadala",
    "धारावी": "Dharavi",
    "प्रभादेवी": "Prabhadevi",
    "खार": "Khar",
    "सांताक्रूझ": "Santacruz",
    "वर्सोवा": "Versova",
    "जोगेश्वरी": "Jogeshwari",
    "दहिसर": "Dahisar",
    "डॉन बॉस्को इन्स्टिट्यूट ऑफ टेक्नॉलॉजी": "Don Bosco Institute of Technology",
    "डॉन बॉस्को": "Don Bosco",
    "मेट्रो स्टेशन": "Metro Station",
    "मेट्रो": "Metro",
    "रेलवे स्टेशन": "Railway Station",
    "रेल्वे स्थानक": "Railway Station",
    "रेल्वे स्टेशन": "Railway Station",
    "रेलवे": "Railway",
    "रेल्वे": "Railway",
    "स्थानक": "Station",
    "स्टेशन": "Station",
    "मार्केट": "Market",
    "बाजार": "Market",
    "बाज़ार": "Market",
    "रोड": "Road",
    "मार्ग": "Marg",
    "रस्ता": "Road",
    "चौक": "Chowk",
    "पूल": "Bridge",
    "रुग्णालय": "Hospital",
    "दवाखाना": "Clinic",
    "शाळा": "School",
    "कॉलेज": "College",
    "इन्स्टिट्यूट": "Institute",
    "विद्यापीठ": "University",
    "उद्यान": "Garden",
    "बाग": "Garden",
    "नगर": "Nagar",
    "नाका": "Naka",
    "डेपो": "Depot",
    "फूटपाथ": "Footpath",
    "पादचारी मार्ग": "Footpath",
    "ऑफ": "of",
    "टेक्नोलॉजी": "Technology",
    "ghatkopar": "Ghatkopar",
    "kurla": "Kurla",
    "borivali": "Borivali",
}


def ward_coord(code):
    return WARD_COORDS.get((code or "").upper(), DEFAULT_MUMBAI)


POSTPOSITION_TRAILING_PATTERNS = [
    # Marathi compound separated
    r"\s+च्या\s+(?:बाहेर|समोर|जवळ|मागे|मध्ये|वर|खाली|तर्फे)$",
    # Hindi compound separated
    r"\s+के\s+(?:पास|बाहर|सामने|पीछे|निकट|नीचे|ऊपर|अंदर|बीच)$",
    # Romanized compound separated
    r"\s+(?:ke\s+(?:paas|pass|bahar|samne|peeche|piche|niche|beech|bich)|chya\s+(?:baher|samor|javal|mage|madhye|madhe|var|khali))$",
    # Marathi single word separated
    r"\s+(?:च्या|बाहेर|समोर|जवळ|मागे|मध्ये|वर|खाली)$",
    # Hindi single word separated
    r"\s+(?:में|पर|से|तक)$",
    # Romanized single word separated
    r"\s+(?:javal|baher|samor|mage|madhye|madhe|var|khali|mein|me|par|se|tak)$",
]

ATTACHED_POSTPOSITION_SUFFIXES = [
    "च्याबाहेर", "च्यासमोर", "च्याजवळ", "च्यामागे", "च्यामध्ये", "च्यावर", "च्याखाली",
    "च्या", "जवळ", "बाहेर", "समोर", "मागे", "मध्ये", "खाली",
    "वर", "ात", "आत",
    "javal", "baher", "samor",
]

MARATHI_OBLIQUE_MAP = {
    "स्थानका": "स्थानक",
    "बाजारा": "बाजार",
    "रुग्णालया": "रुग्णालय",
    "शाळे": "शाळा",
    "चौका": "चौक",
    "पुला": "पूल",
    "मार्गा": "मार्ग",
    "रस्त्या": "रस्ता",
    "नाक्या": "नाका",
}


def _normalize_marathi_oblique(word):
    """Restore base dictionary form from inflected Marathi oblique (सामान्य रूप)."""
    return MARATHI_OBLIQUE_MAP.get(word, word)


def _normalize_postpositions(candidate):
    """Strip attached/separated postpositions and normalize oblique base forms."""
    if not candidate:
        return candidate
    candidate = re.sub(r"\s+", " ", candidate).strip(" .,!?;:-")

    # 1. Separated postpositions at end of phrase
    changed = True
    while changed:
        changed = False
        for pat in POSTPOSITION_TRAILING_PATTERNS:
            m = re.search(pat, candidate, flags=re.IGNORECASE)
            if m:
                candidate = candidate[:m.start()].strip()
                changed = True
                break

    # 2. Attached postpositions on the last word
    words = candidate.split()
    if words:
        last = words[-1]
        for sfx in ATTACHED_POSTPOSITION_SUFFIXES:
            if last.endswith(sfx) and len(last) > len(sfx) + 1:
                stem = last[:-len(sfx)]
                words[-1] = _normalize_marathi_oblique(stem)
                candidate = " ".join(words)
                break
        else:
            normalized_last = _normalize_marathi_oblique(last)
            if normalized_last != last:
                words[-1] = normalized_last
                candidate = " ".join(words)

    return candidate


def _clean_candidate(value):
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip(" .,!?;:-")
    value = _normalize_postpositions(value)
    if not value:
        return None
    # Strip any dangling stop words at boundaries
    words = value.split()
    while words and words[0].lower() in STOP_WORDS:
        words = words[1:]
    while words and words[-1].lower() in STOP_WORDS:
        words = words[:-1]
    value = " ".join(words)
    return value or None


def _strip_problem_words_from_phrase(phrase):
    """Remove problem / complaint words from phrase boundaries."""
    if not phrase:
        return None
    words = phrase.split()
    while words and (words[0].lower() in GENERIC_QUERY_WORDS or words[0].lower() in STOP_WORDS):
        words = words[1:]
    while words and (words[-1].lower() in GENERIC_QUERY_WORDS or words[-1].lower() in STOP_WORDS):
        words = words[:-1]
    if not words:
        return None
    return " ".join(words)


def _clean_connector_target(value):
    """Clean place candidate captured by connector pattern by removing problem words and postpositions."""
    if not value:
        return None
    cleaned = _strip_problem_words_from_phrase(value)
    if not cleaned:
        return None

    # If the phrase still has leading noise preceding a known locality, anchor to the locality
    words = cleaned.split()
    for i in range(len(words)):
        for loc, _ in LOCALITY_TO_WARD:
            loc_words = loc.split()
            if len(loc_words) <= len(words) - i:
                phrase_slice = " ".join(words[i : i + len(loc_words)])
                if phrase_slice.lower() == loc.lower() or _keyword_matches(phrase_slice, loc):
                    if i > 0:
                        cleaned = " ".join(words[i:])
                    break
        else:
            continue
        break

    return _clean_candidate(cleaned)


def _clean_endpoint(value):
    """Strip extraneous leading/trailing complaint or connector words from an endpoint."""
    if not value:
        return None
    val = _strip_problem_words_from_phrase(value)
    val = _clean_candidate(val)
    if not val:
        return None
    # Strip common leading complaint phrases that might precede endpoint A
    for pat in [
        r"^(?:pipeline\s+burst|water\s+leakage|large\s+pothole|huge\s+pothole|pothole|garbage|issue|problem|traffic)\s+(?:at|near|in|on|between)?\s*",
        r"^(?:.*?\s+(?:के\s+कारण|की\s+वजह\s+से|मुळे|कारण|से)\s+)",
        r"^(?:पाइपलाइन\s+फूटने\s+(?:से|के\s+कारण)|कचरा|खड्डा|समस्या|ट्रैफिक)\s*",
    ]:
        val = re.sub(pat, "", val, flags=re.IGNORECASE).strip()

    # Strip any trailing boundary phrase that might linger in endpoint B
    val = re.sub(
        r"\s+(?:(?:" + r"|".join(COMPLAINT_BOUNDARY_WORDS) + r")(?=[\s,.;!?]|$)).*$",
        "",
        val,
        flags=re.IGNORECASE,
    ).strip()
    return _clean_candidate(val)


def _keyword_matches(text, keyword):
    if keyword.isascii():
        return re.search(r"\b" + re.escape(keyword) + r"\b", text, re.IGNORECASE)
    return keyword in text


def infer_ward_from_text(text):
    text_lower = (text or "").lower()
    for keyword, ward_code in LOCALITY_TO_WARD:
        if _keyword_matches(text_lower, keyword.lower()):
            return ward_code
    return None


def _candidate_variants(candidate):
    """Return progressively broader forms, but only drop recognized feature suffixes."""
    if not candidate:
        return []

    def _get_variants(cand):
        var_list = [cand]
        curr = cand.split()
        while len(curr) > 1:
            if curr[-1].lower().strip(".,") in PLACE_FEATURE_WORDS:
                curr = curr[:-1]
                while curr and curr[-1].lower().strip(".,") in STOP_WORDS:
                    curr = curr[:-1]
                shorter = _clean_candidate(" ".join(curr))
                if shorter and shorter not in var_list:
                    var_list.append(shorter)
            else:
                break
        return var_list

    variants = _get_variants(candidate)
    canonical = _canonical_query(candidate)
    if canonical and canonical != candidate:
        for v in _get_variants(canonical):
            if v and v not in variants:
                variants.append(v)

    return [_clean_candidate(v) for v in variants if _clean_candidate(v)]


def _candidate_priority(candidate):
    """Score candidate to prioritize specific place phrases over shorter/locality forms."""
    words = candidate.split()
    feature_count = sum(1 for w in words if w.lower().strip(".,") in PLACE_FEATURE_WORDS)
    feature_bonus = 20 * feature_count
    return feature_bonus + min(len(words), 10)


def _extract_feature_phrase_candidates(text):
    """Extract named-place phrases based on place-feature words when no connector exists."""
    candidates = []
    norm = re.sub(r"\s+", " ", text or "").strip()
    if not norm:
        return candidates

    clauses = re.split(r"[,.;!?\n]+", norm)
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        # Extract phrase preceding any complaint boundary in this clause
        m = re.match(r"^(.+?)(?=" + BOUNDARY_LOOKAHEAD + r")", clause, flags=re.IGNORECASE)
        matched_str = m.group(1) if m else clause
        cleaned = _strip_problem_words_from_phrase(matched_str)
        phrase = _clean_candidate(cleaned)
        if phrase:
            p_words = [w.lower().strip(".,") for w in phrase.split()]
            has_feature = any(w in PLACE_FEATURE_WORDS for w in p_words)
            has_marathi_postpos = any(w.endswith("वर") or w.endswith("च्या") for w in phrase.split())
            is_known_locality = any(_keyword_matches(phrase.lower(), loc.lower()) for loc, _ in LOCALITY_TO_WARD)
            if has_feature or has_marathi_postpos or is_known_locality:
                for v in _candidate_variants(phrase):
                    if v and v not in candidates:
                        candidates.append(v)

    return candidates


def _is_meaningful_candidate(candidate):
    """Filter candidate phrases to ensure they contain distinctive place information."""
    if not candidate:
        return False
    words = [w.lower().strip(".,") for w in candidate.split() if w.lower().strip(".,") not in STOP_WORDS]
    if not words:
        return False
    has_distinctive = any(
        w not in PLACE_FEATURE_WORDS and w not in GENERIC_QUERY_WORDS and len(w) >= 2
        for w in words
    )
    if has_distinctive:
        return True
    for loc, _ in LOCALITY_TO_WARD:
        if candidate.lower() == loc.lower() or _keyword_matches(candidate.lower(), loc.lower()):
            return True
    return False


def _extract_location_candidates(text):
    """Extract bounded place phrases, preferring specific place phrases over locality aliases."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []

    explicit_candidates = []

    # 1. Between X and Y
    for pattern in BETWEEN_PATTERNS:
        match = pattern.search(normalized)
        if match:
            for group in (1, 2):
                cand = _clean_candidate(match.group(group))
                if cand:
                    explicit_candidates.extend(_candidate_variants(cand))

    # 2. Connector targets
    for pattern in CONNECTOR_PATTERNS:
        match = pattern.search(normalized)
        if match:
            cand = _clean_connector_target(match.group(1))
            if cand:
                explicit_candidates.extend(_candidate_variants(cand))

    # Explicit candidates strictly prioritized
    ordered = []
    for candidate in explicit_candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)

    # 3. Place-feature phrase extraction (fallback ONLY when no connector/between)
    feature_phrase_candidates = []
    if not explicit_candidates:
        feature_phrase_candidates = _extract_feature_phrase_candidates(normalized)

    for candidate in feature_phrase_candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)

    # 4. Known locality aliases from LOCALITY_TO_WARD
    locality_candidates = []
    lowered = normalized.lower()
    for locality, _ in LOCALITY_TO_WARD:
        if _keyword_matches(lowered, locality.lower()):
            cand = _clean_candidate(locality)
            if cand and cand not in ordered:
                locality_candidates.append(cand)

    ordered.extend(locality_candidates)

    # If all from fallback or locality, sort by priority
    if not explicit_candidates:
        ordered.sort(key=_candidate_priority, reverse=True)

    ordered = [c for c in ordered if _is_meaningful_candidate(c)]
    logger.debug("LOCATION extracted candidates=%s from text=%r", ordered[:8], text)
    return ordered[:8]


def _normalise_for_match(value):
    return re.sub(r"[^\w\s]", " ", (value or "").casefold())


def _replace_location_aliases(value):
    aliases = sorted(CANONICAL_LOCATION_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)
    lookup = {alias.casefold(): replacement for alias, replacement in aliases}
    patterns = []
    for alias, _replacement in aliases:
        escaped = re.escape(alias)
        patterns.append(r"(?<!\w)" + escaped + r"(?!\w)" if alias.isascii() else escaped)
    if not patterns:
        return value
    matcher = re.compile("|".join(patterns), re.IGNORECASE)
    return matcher.sub(lambda match: lookup[match.group(0).casefold()], value)


def _canonical_query(candidate):
    """Translate known Devanagari/local place aliases to canonical English before search."""
    canonical = _clean_candidate(candidate)
    if not canonical:
        return None
    canonical = _replace_location_aliases(canonical)
    return _clean_candidate(canonical)


_CLUSTER_ISSUE_ALIASES = {
    "pothole": ("pothole", "potholes", "खड्डा", "खड्डे", "खड्ड्यात", "गड्ढा", "गड्ढे", "गड्ढों"),
    "road": ("road", "roads", "roadway", "सड़क", "सड़कें", "रस्ता", "रस्त्यावर", "रस्त्याचा", "रस्त्याची", "रस्त्याचे"),
    "garbage": ("garbage", "rubbish", "waste", "कचरा", "कचऱ्याचा", "कचऱ्याची"),
    "water leakage": ("water leak", "leakage", "पाणी गळती", "पाण्याची गळती", "पाइपलाइन", "पाईपलाईन"),
}


def normalize_cluster_text(text, *, include_issue_tags=False):
    """Canonicalize known local place names and optionally add safe issue tags.

    This is used only to build clustering embeddings. The original complaint,
    severity input, visible text, and stored location are left untouched.
    """
    normalized = _replace_location_aliases(text or "")

    if include_issue_tags:
        lowered = normalized.casefold()
        original_lowered = (text or "").casefold()
        tags = []
        for concept, aliases in _CLUSTER_ISSUE_ALIASES.items():
            if any(
                (
                    re.search(r"\b" + re.escape(alias) + r"\b", lowered, flags=re.IGNORECASE)
                    or re.search(r"\b" + re.escape(alias) + r"\b", original_lowered, flags=re.IGNORECASE)
                    if alias.isascii()
                    else alias in lowered or alias in original_lowered
                )
                for alias in aliases
            ):
                tags.append(concept)
        if tags:
            normalized += "\nCanonical issue: " + ", ".join(tags)
    return normalized


def _candidate_matches_result(candidate, canonical_query, result):
    """Reject in-bounds places that do not plausibly match the requested candidate."""
    address = result.get("address") if isinstance(result.get("address"), dict) else {}
    namedetails = result.get("namedetails") if isinstance(result.get("namedetails"), dict) else {}
    metadata = " ".join(
        str(value)
        for value in (
            result.get("display_name", ""),
            result.get("name", ""),
            *address.values(),
            *namedetails.values(),
        )
    )
    normal_metadata = _normalise_for_match(metadata)
    for evidence_candidate in (candidate, canonical_query):
        normal_candidate = _normalise_for_match(evidence_candidate)
        if normal_candidate and normal_candidate in normal_metadata:
            return True

        terms = re.findall(r"\w+", normal_candidate, flags=re.UNICODE)
        distinctive_terms = [
            term for term in terms
            if term not in PLACE_FEATURE_WORDS and term not in GENERIC_QUERY_WORDS and len(term) >= 2
        ]
        matched_terms = [
            term for term in distinctive_terms
            if re.search(r"\b" + re.escape(term) + r"\b", normal_metadata)
        ]
        if len(distinctive_terms) == 1 and matched_terms:
            return True
        if len(distinctive_terms) > 1 and (
            len(matched_terms) >= 2 or any(len(term) >= 6 for term in matched_terms)
        ):
            return True
    return False


def _nominatim_result_relevant(query, result):
    """Allow a recognized place feature type when Nominatim omits its class."""
    result_class = str(result.get("class") or "").casefold()
    result_type = str(result.get("type") or "").casefold()
    if result_class in LOCATION_CLASSES:
        return True

    known_feature_types = set().union(*FEATURE_CATEGORIES.values())
    normalized_type = _normalise_for_match(result_type.replace("_", " "))
    if any(re.search(r"\b" + re.escape(term) + r"\b", normalized_type) for term in known_feature_types):
        query_words = set(_normalise_for_match(_canonical_query(query) or query).split())
        query_categories = {
            name for name, terms in FEATURE_CATEGORIES.items()
            if query_words & terms
        }
        type_categories = {
            name for name, terms in FEATURE_CATEGORIES.items()
            if any(re.search(r"\b" + re.escape(term) + r"\b", normalized_type) for term in terms)
        }
        return not query_categories or bool(query_categories & type_categories)
    return False


def _nominatim_search(query, timeout):
    """Return one validated, candidate-matching Mumbai result, or None; never raise."""
    global _last_nominatim_request_time

    if not query:
        logger.debug("LOCATION rejected reason=empty_candidate")
        return None

    canonical_query = _canonical_query(query)
    if not canonical_query:
        logger.debug("LOCATION rejected reason=empty_canonical_query")
        return None

    # Check cache and apply thread-safe rate limit before making HTTP request
    with _nominatim_lock:
        if canonical_query in _nominatim_cache:
            cached = _nominatim_cache[canonical_query]
            logger.debug(
                "LOCATION cache hit candidate=%r canonical_query=%r result=%s",
                query,
                canonical_query,
                "found" if cached is not None else "none",
            )
            return dict(cached) if cached is not None else None

        now = time.monotonic()
        elapsed = now - _last_nominatim_request_time
        if elapsed < NOMINATIM_RATE_LIMIT_SECONDS:
            time.sleep(NOMINATIM_RATE_LIMIT_SECONDS - elapsed)
        _last_nominatim_request_time = time.monotonic()

    logger.debug("LOCATION candidate being tried")
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={
                "q": f"{canonical_query}, Mumbai, Maharashtra, India",
                "format": "jsonv2",
                "limit": NOMINATIM_RESULT_LIMIT,
                "addressdetails": 1,
                "namedetails": 1,
                "countrycodes": "in",
                "viewbox": MUMBAI_VIEWBOX,
                "bounded": 0,
            },
            headers={"User-Agent": NOMINATIM_USER_AGENT, "Accept-Language": "en"},
            timeout=timeout,
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError, TypeError, OverflowError) as error:
        logger.debug(
            "LOCATION rejected candidate=%r canonical_query=%r reason=request_or_malformed_response error=%s",
            query,
            canonical_query,
            error,
        )
        return None

    if not isinstance(results, list):
        logger.debug(
            "LOCATION rejected candidate=%r canonical_query=%r reason=malformed_result_format",
            query,
            canonical_query,
        )
        return None

    if not results:
        logger.debug("LOCATION rejected reason=no_results")
        with _nominatim_lock:
            _nominatim_cache[canonical_query] = None
        return None

    for result in results:
        if not isinstance(result, dict):
            logger.debug("LOCATION rejected reason=malformed_result")
            continue
        try:
            lat = float(result["lat"])
            lng = float(result["lon"])
        except (ValueError, TypeError, KeyError, OverflowError):
            logger.debug("LOCATION rejected reason=invalid_coordinates")
            continue

        display_name = str(result.get("display_name", ""))
        result_class = result.get("class")
        result_type = result.get("type")
        importance = result.get("importance")
        logger.debug(
            "LOCATION result candidate=%r display_name=%r class=%r type=%r importance=%r",
            query,
            display_name,
            result_class,
            result_type,
            importance,
        )
        if not math.isfinite(lat) or not math.isfinite(lng):
            logger.debug("LOCATION rejected reason=non_finite_coordinates")
            continue
        if not (MUMBAI_LAT_RANGE[0] <= lat <= MUMBAI_LAT_RANGE[1] and MUMBAI_LNG_RANGE[0] <= lng <= MUMBAI_LNG_RANGE[1]):
            logger.debug(
                "LOCATION rejected reason=outside_mumbai",
            )
            continue
        if not _nominatim_result_relevant(query, result):
            logger.debug(
                "LOCATION rejected reason=irrelevant_class class=%r type=%r",
                result_class,
                result_type,
            )
            continue
        if not _candidate_matches_result(query, canonical_query, result):
            logger.debug(
                "LOCATION rejected reason=name_mismatch",
            )
            continue

        logger.debug(
            "LOCATION accepted class=%r type=%r",
            result_class,
            result_type,
        )
        matched = {
            "lat": lat,
            "lng": lng,
            "display_name": display_name or query,
            "canonical_name": canonical_query,
        }
        with _nominatim_lock:
            _nominatim_cache[canonical_query] = dict(matched)
        return matched

    with _nominatim_lock:
        _nominatim_cache[canonical_query] = None
    return None


def _resolve_endpoint_to_ward(endpoint_text):
    """Resolve an endpoint to (ward_code, canonical_name) or None."""
    if not endpoint_text:
        return None
    cleaned = _clean_endpoint(endpoint_text)
    if not cleaned:
        return None
    cleaned_lower = cleaned.lower()
    for keyword, ward_code in LOCALITY_TO_WARD:
        if _keyword_matches(cleaned_lower, keyword.lower()):
            canonical = CANONICAL_LOCATION_ALIASES.get(keyword, None)
            if not canonical:
                canonical = _canonical_query(keyword)
            if canonical and canonical.isascii():
                canonical = canonical.title()
            elif not canonical:
                canonical = keyword.title()
            return ward_code, canonical
    return None


def resolve_between_ward_fallback(text):
    """Return an unresolved approximation when endpoint locations are ward-only."""
    norm_text = re.sub(r"\s+", " ", text or "").strip()
    if not norm_text:
        return None

    for pattern in BETWEEN_PATTERNS:
        match = pattern.search(norm_text)
        if not match:
            continue
        first_raw = _clean_endpoint(match.group(1))
        second_raw = _clean_endpoint(match.group(2))
        first_info = _resolve_endpoint_to_ward(first_raw)
        second_info = _resolve_endpoint_to_ward(second_raw)

        if first_info and second_info:
            first_ward, first_name = first_info
            second_ward, second_name = second_info
            return {
                "lat": None,
                "lng": None,
                "display_name": f"Approximate area between {first_name} and {second_name}; exact location unresolved",
                "canonical_name": f"{first_name} ↔ {second_name}",
                "source": "ward_midpoint_fallback",
            }

        if first_info or second_info:
            ward, name = first_info or second_info
            return {
                "lat": None,
                "lng": None,
                "display_name": f"Approximate area only (Ward {ward}); exact location unresolved",
                "canonical_name": name,
                "source": "ward_fallback",
            }

        break

    return None


def _evaluate_geoapify_result(candidate, canonical_query, result):
    """Evaluate and validate a single Geoapify geocoding result."""
    try:
        lat = float(result.get("lat"))
        lng = float(result.get("lon"))
    except (TypeError, ValueError):
        return False, 0.0, "invalid_coordinates"

    if not (math.isfinite(lat) and math.isfinite(lng)):
        return False, 0.0, "non_finite_coordinates"

    if not (MUMBAI_LAT_RANGE[0] <= lat <= MUMBAI_LAT_RANGE[1] and MUMBAI_LNG_RANGE[0] <= lng <= MUMBAI_LNG_RANGE[1]):
        return False, 0.0, f"outside_mumbai_bounds (lat={lat:.4f}, lng={lng:.4f})"

    r_name = str(result.get("name") or "")
    r_formatted = str(result.get("formatted") or "")
    r_type = str(result.get("result_type") or "")
    r_cat = str(result.get("category") or "")
    r_meta = _normalise_for_match(
        f"{r_name} {r_formatted} {r_cat} {r_type}".replace("_", " ").replace(".", " ")
    )
    r_primary_meta = _normalise_for_match(
        f"{r_name} {r_cat} {r_type}".replace("_", " ").replace(".", " ")
    )

    q_norm = _normalise_for_match(canonical_query or candidate)
    q_words = [w for w in q_norm.split() if w not in STOP_WORDS and w not in GENERIC_QUERY_WORDS]

    distinctive_terms = [w for w in q_words if w not in ALL_FEATURE_WORDS and len(w) >= 2]
    requested_features = [w for w in q_words if w in ALL_FEATURE_WORDS]

    # 1. Distinctive terms check
    if distinctive_terms:
        matched_distinctive = [w for w in distinctive_terms if re.search(r"\b" + re.escape(w) + r"\b", r_meta)]
        if not matched_distinctive:
            for w in distinctive_terms:
                if len(w) >= 5 and w in r_meta.replace(" ", ""):
                    matched_distinctive.append(w)
        if not matched_distinctive:
            return False, 0.0, f"distinctive_terms_missing: {distinctive_terms} not in metadata"
        distinctive_ratio = len(matched_distinctive) / len(distinctive_terms)
    else:
        distinctive_ratio = 1.0

    # 2. Conflicting feature category check (e.g. education vs worship)
    q_cats = set()
    for cat_name, words in FEATURE_CATEGORIES.items():
        if any(w in requested_features for w in words):
            q_cats.add(cat_name)

    r_cats = set()
    r_primary_cats = set()
    for cat_name, words in FEATURE_CATEGORIES.items():
        if any(re.search(r"\b" + re.escape(w) + r"\b", r_meta) for w in words):
            r_cats.add(cat_name)
        if any(re.search(r"\b" + re.escape(w) + r"\b", r_primary_meta) for w in words):
            r_primary_cats.add(cat_name)

    conflicting_categories = r_cats - q_cats
    if conflicting_categories and q_cats and not (r_cats & q_cats):
        return False, 0.0, f"category_conflict: requested={sorted(q_cats)} returned={sorted(r_cats)}"

    # 3. Requested place feature validation
    feature_score = 0.0
    if requested_features:
        matched_features = [w for w in requested_features if re.search(r"\b" + re.escape(w) + r"\b", r_meta)]
        if not matched_features:
            for w in requested_features:
                if w in r_meta.replace(" ", ""):
                    matched_features.append(w)
        # Feature words in a formatted street address alone do not prove the
        # result itself is the requested station, school, road, etc.
        required_categories = q_cats
        missing_categories = required_categories - r_primary_cats
        if missing_categories:
            return False, 0.0, f"feature_category_mismatch: result itself lacks {sorted(missing_categories)}"
        if matched_features:
            feature_score = len(matched_features) / len(requested_features)
        else:
            return False, 0.0, f"feature_mismatch: requested {requested_features} not found in result"
    else:
        feature_score = 0.2

    # 4. Name match score
    name_norm = _normalise_for_match(r_name)
    if q_norm == name_norm:
        name_score = 0.4
    elif q_norm in name_norm or name_norm in q_norm:
        name_score = 0.3
    elif q_norm in r_meta:
        name_score = 0.2
    else:
        name_score = 0.1

    final_score = 0.4 * distinctive_ratio + name_score + 0.3 * feature_score
    accepted = final_score >= 0.5
    rejection_reason = "none" if accepted else f"score_too_low: {final_score:.2f} < 0.50"
    return accepted, final_score, rejection_reason


def _geoapify_search(query, timeout=GEOAPIFY_TIMEOUT_SECONDS):
    """Query Geoapify Forward Geocoding API with validation; returns normalized result or None."""
    api_key = os.getenv("GEOAPIFY_API_KEY")
    if not api_key:
        logger.debug("LOCATION provider=geoapify skipped reason=missing_api_key")
        return None
    if not query:
        return None
    canonical = _canonical_query(query)
    if not canonical:
        return None

    with _geoapify_lock:
        if canonical in _geoapify_cache:
            cached = _geoapify_cache[canonical]
            logger.debug("LOCATION provider=geoapify cache_hit=true")
            return dict(cached) if cached else None

    logger.debug("LOCATION provider=geoapify cache_hit=false")
    params = {
        "text": canonical,
        "filter": GEOAPIFY_RECT,
        "bias": GEOAPIFY_RECT,
        "limit": GEOAPIFY_RESULT_LIMIT,
        "format": "json",
        "apiKey": api_key,
    }
    any_request_failed = False
    genuine_no_result = False
    try:
        response = _geoapify_session.get(GEOAPIFY_URL, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            logger.debug(
                "LOCATION provider=geoapify rejected candidate=%r canonical_query=%r reason=malformed_response",
                query,
                canonical,
            )
            return None
        results = data["results"]
    except (requests.RequestException, ValueError, TypeError) as error:
        logger.debug(
            "LOCATION provider=geoapify request failed candidate=%r canonical_query=%r error=%s",
            query,
            canonical,
            error,
        )
        any_request_failed = True
        results = None

    best_result = None
    best_score = -1.0
    if results is not None:
        genuine_no_result = not results
        for result in results:
            if not isinstance(result, dict):
                logger.debug(
                    "LOCATION provider=geoapify rejected candidate=%r canonical_query=%r reason=malformed_result",
                    query,
                    canonical,
                )
                continue
            returned_name = str(result.get("name") or "")
            returned_address = str(result.get("formatted") or "")
            result_type = str(result.get("result_type") or "")
            result_category = str(result.get("category") or "")
            accepted, score, reason = _evaluate_geoapify_result(query, canonical, result)
            logger.debug(
                "LOCATION provider=geoapify candidate=%r canonical_query=%r returned_name=%r "
                "returned_address=%r type=%r category=%r validation_score=%.2f accepted=%s reason=%s",
                query,
                canonical,
                returned_name,
                returned_address,
                result_type,
                result_category,
                score,
                accepted,
                reason,
            )
            if accepted and score > best_score:
                best_score = score
                best_result = result

    if best_result:
        matched = {
            "lat": float(best_result["lat"]),
            "lng": float(best_result["lon"]),
            "display_name": best_result.get("formatted") or best_result.get("name") or canonical,
            "canonical_name": canonical,
            "source": "geoapify",
            "place_id": best_result.get("place_id"),
            "returned_name": best_result.get("name"),
            "formatted_address": best_result.get("formatted"),
            "result_type": best_result.get("result_type"),
            "category": best_result.get("category"),
            "validation_score": best_score,
        }
        with _geoapify_lock:
            _geoapify_cache[canonical] = dict(matched)
        return matched

    # Cache only a genuine empty result list. A request error, malformed result,
    # or non-empty list rejected by validation can change on a later request.
    if genuine_no_result and not any_request_failed:
        with _geoapify_lock:
            _geoapify_cache[canonical] = None
    return None


def _resolve_candidate_variants(candidate, timeout=GEOAPIFY_TIMEOUT_SECONDS):
    """Try each candidate at full specificity with Geoapify then Nominatim.

    Only after both providers reject a variant do we proceed to its broader form.
    """
    variants = _candidate_variants(candidate)
    for index, variant in enumerate(variants):
        logger.debug(
            "LOCATION candidate=%r variant=%r specificity=%s",
            candidate,
            variant,
            "exact" if index == 0 else "broader",
        )
        result = _geoapify_search(variant, timeout)
        if result:
            result["candidate_attempted"] = variant
            return result

        result = _nominatim_search(variant, timeout=min(timeout, 5))
        if result:
            result["source"] = "nominatim"
            result["candidate_attempted"] = variant
            return result
    return None


def geocode_via_nominatim(text, timeout=4):
    return resolve_location(text, timeout)


# Groq is intentionally the primary language-understanding layer.  The legacy
# regex extractor above is retained only when the AI response is unavailable or
# invalid, and the locality-to-ward table is retained only for offline fallback.
LOCATION_AI_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["has_location", "is_between", "locations"],
    "properties": {
        "has_location": {"type": "boolean"},
        "is_between": {"type": "boolean"},
        "locations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["mention", "normalized_name", "role", "relation", "context", "location_type", "confidence"],
                "properties": {
                    "mention": {"type": "string"},
                    "normalized_name": {"type": "string"},
                    "role": {"type": "string"},
                    "relation": {"type": "string"},
                    "context": {"type": "string"},
                    "location_type": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}

_location_ai_cache = {}
_location_ai_lock = threading.Lock()
_LOCATION_AI_SYSTEM_PROMPT = """Extract ONLY actual place entities from a civic complaint in English, Hindi, Marathi, romanized Hindi/Marathi, or mixed language. Return the schema exactly. A primary specific entity must remain primary; a surrounding locality belongs in context and must never replace it. Understand relationships such as near, outside, opposite, behind, beside, in, and between in all supplied languages and romanizations. For a between complaint return exactly two endpoint entities with roles endpoint_a and endpoint_b. Do not invent names. A generic unnamed category such as 'a hospital', 'the market', or 'an unnamed road' is not a resolvable named place: use has_location false unless a named locality is explicitly present, in which case extract only that named locality. Use a broad, closest location_type, not a fixed vocabulary. confidence is 0 through 1."""


def _valid_location_ai_payload(payload):
    """Validate before trusting external structured output; never raise."""
    if not isinstance(payload, dict) or set(payload) != {"has_location", "is_between", "locations"}:
        return None
    if not isinstance(payload["has_location"], bool) or not isinstance(payload["is_between"], bool) or not isinstance(payload["locations"], list):
        return None
    required = {"mention", "normalized_name", "role", "relation", "context", "location_type", "confidence"}
    locations = []
    for item in payload["locations"]:
        if not isinstance(item, dict) or set(item) != required:
            return None
        if not all(isinstance(item[key], str) for key in required - {"confidence"}):
            return None
        if not isinstance(item["confidence"], (int, float)) or not math.isfinite(float(item["confidence"])):
            return None
        if not 0 <= float(item["confidence"]) <= 1 or not item["normalized_name"].strip():
            return None
        locations.append({**item, "confidence": float(item["confidence"])})
    if payload["has_location"] != bool(locations):
        return None
    if payload["is_between"] and len(locations) != 2:
        return None
    return {"has_location": payload["has_location"], "is_between": payload["is_between"], "locations": locations}


def _extract_locations_with_groq(text, timeout=6):
    """Return validated structured extraction, or None so normal fallback can proceed."""
    if not text or Groq is None or not os.getenv("GROQ_API_KEY"):
        logger.debug("LOCATION AI unavailable reason=%s", "empty_text_or_missing_client_or_key")
        return None
    cache_key = text.strip()
    with _location_ai_lock:
        cached = _location_ai_cache.get(cache_key)
    if cached is not None:
        return dict(cached)
    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"), timeout=timeout, max_retries=0)
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "system", "content": _LOCATION_AI_SYSTEM_PROMPT}, {"role": "user", "content": text}],
            temperature=0,
            response_format={"type": "json_schema", "json_schema": {"name": "civic_location_extraction", "strict": True, "schema": LOCATION_AI_SCHEMA}},
        )
    except APITimeoutError:
        logger.debug("LOCATION AI ERROR provider=groq type=timeout")
        return None
    except APIConnectionError:
        logger.debug("LOCATION AI ERROR provider=groq type=connection_error")
        return None
    except APIStatusError as error:
        status_code = getattr(error, "status_code", None)
        error_type = {
            400: "invalid_request",
            401: "authentication",
            403: "forbidden",
            429: "rate_limit",
            500: "server_error",
            502: "server_error",
            503: "server_error",
            504: "server_error",
        }.get(status_code, "api_status_error")
        logger.debug("LOCATION AI ERROR provider=groq status=%s type=%s", status_code, error_type)
        return None
    except Exception:  # Provider errors must never reject a complaint.
        logger.debug("LOCATION AI ERROR provider=groq type=unexpected_error")
        return None

    logger.debug("LOCATION AI SUCCESS provider=groq model=openai/gpt-oss-20b status=200")
    try:
        content = response.choices[0].message.content
        extracted = _valid_location_ai_payload(json.loads(content))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        logger.debug("LOCATION AI ERROR provider=groq type=malformed_structured_response")
        return None
    if extracted is None:
        logger.debug("LOCATION AI ERROR provider=groq type=malformed_structured_response")
        return None
    logger.debug("LOCATION AI extracted location_count=%s", len(extracted["locations"]))
    with _location_ai_lock:
        _location_ai_cache[cache_key] = dict(extracted)
    return extracted


def _structured_query(entity, include_context=True):
    """Keep the specific entity first; context is a geographic disambiguator."""
    primary = _canonical_query(entity.get("normalized_name") or entity.get("mention"))
    context = _canonical_query(entity.get("context", "")) if include_context else None
    if not primary:
        return None
    components = [primary]
    if context and _normalise_for_match(context) != _normalise_for_match(primary):
        components.append(context)
    components.extend(["Mumbai", "Maharashtra", "India"])
    return ", ".join(components)


def _entity_tokens(value):
    return {token for token in re.findall(r"\w+", _normalise_for_match(value), flags=re.UNICODE)
            if token not in STOP_WORDS and token not in GENERIC_QUERY_WORDS and len(token) > 1}


def _location_type_matches(entity_type, result_type, category, metadata):
    """Broad type compatibility, deliberately generic rather than landmark rules."""
    requested = _normalise_for_match(entity_type).replace("_", " ")
    returned = _normalise_for_match(" ".join([result_type or "", category or "", metadata or ""]).replace("_", " "))
    family = {
        "railway station": ("railway", "station", "train"), "metro station": ("metro", "station"),
        "transit stop": ("station", "stop", "bus", "transit", "railway"), "college": ("college", "university", "education"),
        "institute": ("institute", "education", "college"), "university": ("university", "college", "education"),
        "school": ("school", "education"), "hospital": ("hospital", "clinic", "health"), "clinic": ("clinic", "hospital", "health"),
        "market": ("market", "commercial", "shop"), "mall": ("mall", "commercial", "shop"),
        "road": ("road", "street", "highway"), "street": ("street", "road", "highway"), "bridge": ("bridge",),
        "flyover": ("flyover", "bridge", "road"), "park": ("park", "garden", "leisure"), "garden": ("garden", "park", "leisure"),
        "temple": ("temple", "place of worship"), "mosque": ("mosque", "place of worship"), "church": ("church", "place of worship"),
        "building": ("building",), "locality": ("locality", "neighbourhood", "suburb", "district", "city"),
        "neighborhood": ("neighbourhood", "neighborhood", "locality", "suburb"), "suburb": ("suburb", "locality", "neighbourhood"),
        "address": ("address", "house", "building"),
    }
    options = family.get(requested)
    return True if not options or requested in {"unknown", "landmark", "place"} else any(option in returned for option in options)


def _score_structured_geoapify(entity, result):
    try:
        lat, lng = float(result.get("lat")), float(result.get("lon"))
    except (TypeError, ValueError):
        return False, 0.0, "invalid_coordinates"
    if not (math.isfinite(lat) and math.isfinite(lng) and MUMBAI_LAT_RANGE[0] <= lat <= MUMBAI_LAT_RANGE[1] and MUMBAI_LNG_RANGE[0] <= lng <= MUMBAI_LNG_RANGE[1]):
        return False, 0.0, "outside_mumbai_bounds"
    name = str(result.get("name") or "")
    formatted = str(result.get("formatted") or "")
    category, result_type = str(result.get("category") or ""), str(result.get("result_type") or "")
    metadata = " ".join((name, formatted, category, result_type))
    primary_tokens, context_tokens = _entity_tokens(entity.get("normalized_name", "")), _entity_tokens(entity.get("context", ""))
    result_tokens = _entity_tokens(metadata)
    primary_overlap = len(primary_tokens & result_tokens) / max(1, len(primary_tokens))
    context_overlap = len(context_tokens & result_tokens) / max(1, len(context_tokens)) if context_tokens else 1.0
    if primary_tokens and primary_overlap < (1.0 if len(primary_tokens) == 1 else 0.5):
        return False, 0.0, "primary_entity_tokens_missing"
    if context_tokens and context_overlap == 0:
        return False, 0.0, "context_tokens_missing"
    if not _location_type_matches(entity.get("location_type", "unknown"), result_type, category, metadata):
        return False, 0.0, "requested_type_mismatch"
    rank = result.get("rank") if isinstance(result.get("rank"), dict) else {}
    try:
        confidence = float(rank.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    match_bonus = 0.1 if str(rank.get("match_type", "")).casefold() in {"full_match", "exact_match"} else 0.0
    full_name = _normalise_for_match(entity.get("normalized_name", ""))
    name_similarity = 1.0 if full_name and full_name in _normalise_for_match(name) else primary_overlap
    score = 0.42 * primary_overlap + 0.18 * context_overlap + 0.20 * name_similarity + 0.12 * max(0.0, min(confidence, 1.0)) + match_bonus
    return score >= 0.68, score, "accepted" if score >= 0.68 else "score_too_low"


def _geoapify_structured_search(entity, timeout):
    api_key = os.getenv("GEOAPIFY_API_KEY")
    primary_query = _canonical_query(entity.get("normalized_name") or entity.get("mention"))
    contextual_query = _structured_query(entity)
    if not primary_query or not api_key:
        return None
    # Never discard explicit locality context during geocoder fallback.
    queries = [contextual_query or primary_query]
    for query in queries:
        cache_key = "structured:" + query
        with _geoapify_lock:
            cached = _geoapify_cache.get(cache_key)
        if cached is not None:
            return dict(cached)
        logger.debug("LOCATION Nominatim query started")
        try:
            response = _geoapify_session.get(GEOAPIFY_URL, params={"text": query, "filter": GEOAPIFY_RECT, "bias": GEOAPIFY_RECT, "limit": GEOAPIFY_RESULT_LIMIT, "format": "json", "apiKey": api_key}, timeout=timeout)
            response.raise_for_status()
            results = response.json().get("results", [])
            if not isinstance(results, list):
                continue
        except (requests.RequestException, ValueError, TypeError) as error:
            logger.debug("LOCATION GEOAPIFY failed error=%s", type(error).__name__)
            continue
        best, best_score = None, -1.0
        for result in results:
            if not isinstance(result, dict):
                continue
            accepted, score, reason = _score_structured_geoapify(entity, result)
            logger.debug("LOCATION GEOAPIFY returned_name=%r returned_address=%r result_type=%r confidence=%r score=%.2f accepted=%s reason=%s", result.get("name"), result.get("formatted"), result.get("result_type"), (result.get("rank") or {}).get("confidence"), score, accepted, reason)
            if accepted and score > best_score:
                best, best_score = result, score
        if best:
            matched = {"lat": float(best["lat"]), "lng": float(best["lon"]), "display_name": best.get("formatted") or best.get("name"), "canonical_name": entity.get("normalized_name"), "source": "geoapify"}
            with _geoapify_lock:
                _geoapify_cache[cache_key] = dict(matched)
            return matched
    return None


def _structured_nominatim_matches(entity, result):
    """Apply the same primary/context guard after Nominatim's legacy checks."""
    metadata = " ".join(str(value) for value in (
        result.get("display_name", ""), result.get("canonical_name", ""),
    ))
    result_tokens = _entity_tokens(metadata)
    primary_tokens = _entity_tokens(entity.get("normalized_name", ""))
    context_tokens = _entity_tokens(entity.get("context", ""))
    primary_overlap = len(primary_tokens & result_tokens) / max(1, len(primary_tokens))
    context_overlap = len(context_tokens & result_tokens) / max(1, len(context_tokens)) if context_tokens else 1.0
    accepted = primary_overlap >= (1.0 if len(primary_tokens) == 1 else 0.5) and (not context_tokens or context_overlap > 0)
    logger.debug("LOCATION NOMINATIM primary_overlap=%.2f context_overlap=%.2f accepted=%s", primary_overlap, context_overlap, accepted)
    return accepted


def _resolve_structured_entity(entity, timeout):
    logger.debug("LOCATION AI resolving type=%r relation=%r", entity.get("location_type"), entity.get("relation"))
    result = _geoapify_structured_search(entity, timeout)
    if result:
        return result
    # Nominatim retains its existing Mumbai bounds, cache, rate limit, and result validation.
    query = _structured_query(entity)
    if query:
        result = _nominatim_search(query, min(timeout, 5))
        if result and _structured_nominatim_matches(entity, result):
            result["source"] = "nominatim"
            return result
        if result:
            logger.debug("LOCATION NOMINATIM accepted_by_legacy_validation=false reason=structured_entity_mismatch")
    return None


def _ward_fallback_result(text):
    ward_code = infer_ward_from_text(text)
    if not ward_code:
        return None
    return {"lat": None, "lng": None, "display_name": f"Approximate area only (Ward {ward_code}); exact location unresolved", "canonical_name": ward_code, "source": "ward_fallback", "ward_code": ward_code}


def _structured_ward_endpoint(entity):
    """Last-resort endpoint resolution using only known localities, never a default point."""
    ward_code = infer_ward_from_text(entity.get("normalized_name", ""))
    if not ward_code:
        return None
    return {"lat": None, "lng": None, "display_name": f"Approximate area only near {entity['normalized_name']} (Ward {ward_code}); exact location unresolved", "canonical_name": entity["normalized_name"], "source": "ward_fallback", "ward_code": ward_code}


def _retain_complaint_locality(entity, text):
    """Fill missing extractor context from named locality text in the complaint."""
    if entity.get("context", "").strip():
        return entity
    for locality, _ward in LOCALITY_TO_WARD:
        if _keyword_matches((text or "").casefold(), locality.casefold()):
            return {**entity, "context": locality}
    return entity


def resolve_location(text, timeout=GEOAPIFY_TIMEOUT_SECONDS):
    """Text-only location resolution: Groq -> Geoapify -> Nominatim -> ward -> unknown."""
    extraction = _extract_locations_with_groq(text, timeout=min(timeout, 6))
    structured_between_endpoints = None
    if extraction and extraction["has_location"]:
        if extraction["is_between"]:
            endpoints = sorted((_retain_complaint_locality(item, text) for item in extraction["locations"]), key=lambda item: 0 if item["role"] == "endpoint_a" else 1)
            first, second = (_resolve_structured_entity(item, timeout) for item in endpoints)
            if first and second:
                sources = {first["source"], second["source"]}
                source = "geoapify_midpoint" if "geoapify" in sources else "nominatim_midpoint"
                final = {"lat": (first["lat"] + second["lat"]) / 2, "lng": (first["lng"] + second["lng"]) / 2, "display_name": f"Approximate midpoint between {endpoints[0]['normalized_name']} and {endpoints[1]['normalized_name']}", "canonical_name": f"{endpoints[0]['normalized_name']} ↔ {endpoints[1]['normalized_name']}", "source": source}
                logger.debug("LOCATION FINAL source=%s coordinates_available=true", final["source"])
                return final
            structured_between_endpoints = endpoints
        else:
            primary = next((item for item in extraction["locations"] if item["role"] == "primary"), extraction["locations"][0])
            primary = _retain_complaint_locality(primary, text)
            result = _resolve_structured_entity(primary, timeout)
            if result:
                logger.debug("LOCATION FINAL source=%s coordinates_available=true", result["source"])
                return result

    # Groq may understand a location that neither geographic provider can resolve.
    # In that case (and when Groq is unavailable), reuse the legacy extractor before
    # falling back to ward-level coordinates.
    for candidate in _extract_location_candidates(text):
        result = _resolve_candidate_variants(candidate, timeout=timeout)
        if result:
            logger.debug("LOCATION rule_based_linguistic_fallback source=%s", result.get("source"))
            return result

    if structured_between_endpoints:
        first, second = (_structured_ward_endpoint(item) for item in structured_between_endpoints)
        if first and second:
            final = {"lat": None, "lng": None, "display_name": f"Approximate area between {structured_between_endpoints[0]['normalized_name']} and {structured_between_endpoints[1]['normalized_name']}; exact location unresolved", "canonical_name": f"{structured_between_endpoints[0]['normalized_name']} ↔ {structured_between_endpoints[1]['normalized_name']}", "source": "ward_fallback", "ward_code": first["ward_code"] if first["ward_code"] == second["ward_code"] else None}
            logger.debug("LOCATION FINAL source=%s coordinates_available=false", final["source"])
            return final
    fallback = _ward_fallback_result(text)
    if fallback:
        logger.debug("LOCATION FINAL source=%s coordinates_available=false", fallback["source"])
        return fallback
    logger.debug("LOCATION FINAL source=none coordinates_available=false")
    return {"lat": None, "lng": None, "display_name": None, "canonical_name": None, "source": "none"}
