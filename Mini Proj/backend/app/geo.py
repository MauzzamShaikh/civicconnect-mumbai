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
GEOAPIFY_RESULT_LIMIT = 10
GEOAPIFY_TIMEOUT_SECONDS = 15
GEOAPIFY_RECT = f"rect:{MUMBAI_LNG_RANGE[0]},{MUMBAI_LAT_RANGE[0]},{MUMBAI_LNG_RANGE[1]},{MUMBAI_LAT_RANGE[1]}"
PHOTON_URL = "https://photon.komoot.io/api/"
PHOTON_RATE_LIMIT_SECONDS = 1.0
MAX_LANDMARK_TO_LOCALITY_KM = 2.0

# Parent locality terms improve provider search ranking for Mumbai sub-localities.
# They are query hints only; validation still requires the specific named place
# and the complaint's own locality to match.
LOCALITY_PARENT_CONTEXT = {
    "Versova": "Andheri West",
    "Malad West": "Malad",
    "Malad East": "Malad",
    "Kurla West": "Kurla",
    "Kurla East": "Kurla",
    "Vile Parle": "Mumbai Western Suburbs",
    "Vidyavihar": "Ghatkopar",
    "Vidyavihar West": "Vidyavihar",
    "Masjid Bunder": "South Mumbai",
}
LOCALITY_POSTCODES = {
    "Chembur": {"400088"},
    "Malad West": {"400064"},
    "Malad East": {"400097"},
    "Vidyavihar West": {"400086"},
    "Vidyavihar": {"400077", "400086"},
    "Matunga": {"400019"},
    "Bandra West": {"400050"},
    "Bandra East": {"400051"},
    "Vile Parle": {"400056", "400057"},
}

_nominatim_lock = threading.Lock()
_last_nominatim_request_time = 0.0
_nominatim_cache = {}

_geoapify_lock = threading.Lock()
_geoapify_cache = {}
_geoapify_session = requests.Session()
_photon_lock = threading.Lock()
_last_photon_request_time = 0.0
_photon_cache = {}
_photon_session = requests.Session()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False
if not any(getattr(handler, "_bmc_location_diagnostics", False) for handler in logger.handlers):
    _location_log_handler = logging.StreamHandler()
    _location_log_handler.setLevel(logging.INFO)
    _location_log_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    _location_log_handler._bmc_location_diagnostics = True
    logger.addHandler(_location_log_handler)

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
    # Bandra Railway Station is on the western side. Keep the general locality
    # after the more specific east/west entries so those retain precedence.
    ("bandra", "H/W"),
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
    # Marathi/Hindi often introduce the problem after the landmark with
    # "एक ..." (for example, "metro stationबाहेर एक illegal stand").
    r"एक",
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
        r"(.+?)(?:च्याबाहेर|च्यासमोर|च्याजवळ|च्यामागे|च्यामध्ये|च्यावर|च्याखाली|जवळील|बाहेरील|समोरील|मागील|मधील|जवळ(?:चा|ची|चे)?|बाहेर|समोर|मागे|मध्ये|वर|खाली)" + POST_BOUNDARY,
        re.IGNORECASE,
    ),
    # Directional reports (toward a place) are a single location, not a
    # report between two endpoints. Keep the entire named target for lookup.
    re.compile(r"(.+?)\s+(?:की\s+(?:ओर|तरफ|दिशा\s+में)|की\s+दिशा\s+में)" + POST_BOUNDARY, re.IGNORECASE),
    re.compile(r"(.+?)(?:च्या\s+दिशेने|कडे)" + POST_BOUNDARY, re.IGNORECASE),
    # Romanized connectors: place + ke paas / chya baher / javal / etc.
    re.compile(
        r"\b(.+?)\s+(?:ke\s+(?:paas|pass|bahar|samne|peeche|piche|niche)|chya\s+(?:baher|samor|javal|mage|madhye|madhe|var|khali)|ki\s+(?:or|taraf)|dishene|kade|javal|baher|samor|mage|madhye|madhe)" + POST_BOUNDARY,
        re.IGNORECASE,
    ),
]

PLACE_FEATURE_WORDS = {
    "station", "railway", "metro", "market", "road", "marg", "lane", "nagar",
    "chowk", "bridge", "hospital", "हॉस्पिटल", "अस्पताल", "रुग्णालय", "दवाखाना", "school", "college", "institute", "university",
    "temple", "mosque", "church", "garden", "park", "mall", "depot", "stop",
    "flyover", "highway", "footpath", "circle", "gali", "bazaar", "technology",
    "स्टेशन", "स्थानक", "रोड", "मार्ग", "रस्ता", "चौक", "पूल", "मार्केट", "बाजार",
    "रुग्णालय", "दवाखाना", "शाळा", "कॉलेज", "इन्स्टिट्यूट", "विद्यापीठ", "मंदिर",
    "मशीद", "उद्यान", "बाग", "गल्ली", "नगर", "नाका", "डेपो", "फूटपाथ", "टेक्नोलॉजी", "टेक्नॉलॉजी",
    "रेल्वे", "रेलवे",
    "sthanak", "rasta", "pool", "pul", "dawakana", "shala", "mandir", "masjid",
    "udyan", "bagh", "naka",
    # Common civic and public-service facilities. Keeping these generic feature
    # words out of identity scoring lets the actual landmark name drive matching.
    "police", "station", "post", "office", "fire", "brigade", "library", "museum",
    "bank", "atm", "court", "school", "university", "airport", "terminal",
    "bus", "depot", "stadium", "playground", "community", "health", "centre", "center",
    "पोलीस", "पुलिस", "थाना", "चौकी", "पोस्ट", "कार्यालय", "अग्निशमन", "ग्रंथालय", "पुस्तकालय",
    "बँक", "बैंक", "न्यायालय", "विमानतळ", "हवाईअड्डा", "हवाई", "क्रीडांगण", "मैदान",
    "police", "thana", "chowky", "library", "museum", "bank", "airport", "stadium",
}

GENERIC_QUERY_WORDS = {
    # English problem / adjective words
    "pothole", "potholes", "garbage", "waste", "trash", "debris", "water", "pipeline",
    "leakage", "footpath", "footpaths", "blocked", "blocking", "broken", "damaged",
    "large", "huge", "small", "big", "deep", "there", "issue", "issues",
    "entrance", "entrances", "gate", "gates", "प्रवेशद्वार", "द्वार", "गेट",
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
    # A broad, reusable category for public-service POIs that are not transit,
    # medical, or education locations. It is based on OSM-style feature names,
    # not a list of individual Mumbai landmarks.
    "public_service": {
        "police", "fire", "brigade", "post", "office", "library", "museum", "bank",
        "court", "government", "municipal", "पोलीस", "पुलिस", "थाना", "चौकी", "डाकघर",
    },
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
    "vidya vihar": "Vidyavihar",
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
    # Frequently reported public landmarks; canonical names follow common map labels.
    "shah and anchor college": "Shah & Anchor Kutchhi Engineering College",
    "shah & anchor college": "Shah & Anchor Kutchhi Engineering College",
    "shah anchor college": "Shah & Anchor Kutchhi Engineering College",
    "शाह एंड एंकर कॉलेज": "Shah & Anchor Kutchhi Engineering College",
    "शाह और एंकर कॉलेज": "Shah & Anchor Kutchhi Engineering College",
    "शाह एवं एंकर कॉलेज": "Shah & Anchor Kutchhi Engineering College",
    "शाह अँड अँकर कॉलेज": "Shah & Anchor Kutchhi Engineering College",
    "शाह आणि अँकर कॉलेज": "Shah & Anchor Kutchhi Engineering College",
    "शाह आणि अँकर महाविद्यालय": "Shah & Anchor Kutchhi Engineering College",
    "mittal college": "K G Mittal College",
    "kg mittal college": "K G Mittal College",
    "के जी मित्तल कॉलेज": "K G Mittal College",
    "केजी मित्तल कॉलेज": "K G Mittal College",
    "मित्तल कॉलेज": "K G Mittal College",
    "मित्तल महाविद्यालय": "K G Mittal College",
    "fatima high school": "Fatima High School",
    "फातिमा हाई स्कूल": "Fatima High School",
    "फातिमा हायस्कूल": "Fatima High School",
    "shah and anchor kutchhi engineering college": "Shah & Anchor Kutchhi Engineering College",
    "don bosco institute of technology": "Don Bosco Institute of Technology",
    "don bosco institute of technology mumbai": "Don Bosco Institute of Technology",
    "डॉन बॉस्को इन्स्टिट्यूट ऑफ टेक्नॉलॉजी": "Don Bosco Institute of Technology",
    "डॉन बॉस्को इंस्टिट्यूट ऑफ टेक्नोलॉजी": "Don Bosco Institute of Technology",
    "डॉन बॉस्को इंस्टीट्यूट ऑफ टेक्नोलॉजी": "Don Bosco Institute of Technology",
    "डॉन बॉस्को संस्थान": "Don Bosco Institute of Technology",
    "dj sanghvi college": "Dwarkadas J Sanghvi College of Engineering",
    "d j sanghvi college": "Dwarkadas J Sanghvi College of Engineering",
    "डी जे सांघवी कॉलेज": "Dwarkadas J Sanghvi College of Engineering",
    "डी. जे. सांघवी कॉलेज": "Dwarkadas J Sanghvi College of Engineering",
    "डीजे सांघवी कॉलेज": "Dwarkadas J Sanghvi College of Engineering",
    "डी जे संघवी कॉलेज": "Dwarkadas J Sanghvi College of Engineering",
    "व्हीजेटीआय कॉलेज": "Veermata Jijabai Technological Institute",
    "वीजेटीआई कॉलेज": "Veermata Jijabai Technological Institute",
    "वीजेतीआई कॉलेज": "Veermata Jijabai Technological Institute",
    "veermata jijabai technological institute": "Veermata Jijabai Technological Institute",
    "veer mata jijabai technological institute": "Veermata Jijabai Technological Institute",
    "veermata jijabai technology institute": "Veermata Jijabai Technological Institute",
    "वीरमाता जिजाबाई तंत्रज्ञान संस्था": "Veermata Jijabai Technological Institute",
    "वीरमाता जिजाबाई प्रौद्योगिकी संस्थान": "Veermata Jijabai Technological Institute",
    "vjti college": "Veermata Jijabai Technological Institute",
    "vjti": "Veermata Jijabai Technological Institute",
    "bandra railway station": "Bandra Railway Station",
    "बांद्रा रेलवे स्टेशन": "Bandra Railway Station",
    "बांद्रा रेल्वे स्थानक": "Bandra Railway Station",
    "बांद्रा रेल्वे स्टेशन": "Bandra Railway Station",
    "बांद्रा रेल्वे स्थानकावर": "Bandra Railway Station",
    "बांद्रा रेलवे स्टेशन पर": "Bandra Railway Station",
    "versova metro station": "Versova Metro Station",
    "वर्सोवा मेट्रो स्टेशन": "Versova Metro Station",
    # Santacruz station reports arrive in Hindi and Marathi spellings and
    # provider labels may omit "Railway". Normalize them to the same named
    # transit site so a citizen pin can safely join the text-only cluster.
    "santa cruz railway station": "Santacruz Railway Station",
    "santa cruz station": "Santacruz Railway Station",
    "santacruz station": "Santacruz Railway Station",
    "सांताक्रूज रेलवे स्टेशन": "Santacruz Railway Station",
    "सांताक्रूज स्टेशन": "Santacruz Railway Station",
    "सांताक्रूज रेल्वे स्थानक": "Santacruz Railway Station",
    "सांताक्रूज रेल्वे स्थानकाबाहेर": "Santacruz Railway Station",
    "सांताक्रूझ रेल्वे स्थानक": "Santacruz Railway Station",
    "जेबी नगर": "JB Nagar",
    "ज बी नगर": "JB Nagar",
    "infinity mall": "Infiniti Mall",
    "phoenix market city": "Phoenix Marketcity",
    "phoenix marketcity": "Phoenix Marketcity",
    "फिनिक्स मार्केटसिटी": "Phoenix Marketcity",
    "फिनिक्स मार्केट सिटी": "Phoenix Marketcity",
    "इन्फिनिटी मॉल": "Infiniti Mall",
    "इनफिनिटी मॉल": "Infiniti Mall",
    "होली क्रॉस": "Holy Cross",
    "होली क्रॉस शाळे": "Holy Cross School",
    "होली क्रॉस शाळा": "Holy Cross School",
    "होली क्रॉस स्कूल": "Holy Cross School",
    "होली क्रॉस शाळेबाहेर": "Holy Cross School",
    "होली क्रॉस शाळेबाहेरील": "Holy Cross School",
    "railway station": "Railway Station",
    "गोरेगाव": "Goregaon",
    "बोरीवली": "Borivali",
    "कांदिवली": "Kandivali",
    "विखरोली": "Vikhroli",
    "मानखुर्द": "Mankhurd",
    "गोवंडी": "Govandi",
    "साकीनाका": "Sakinaka",
    "हॉस्पिटल": "Hospital",
    "अस्पताल": "Hospital",
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
    # Keep transliterated and local-language names for the same hospital
    # consistent across the three submission channels. Some geocoders label
    # its nearby address as "Sion Hospital Quarters"; that is still the named
    # Sion Hospital site for clustering purposes.
    "sion hospital quarters": "Sion Hospital",
    "sayan hospital": "Sion Hospital",
    "sion hospital": "Sion Hospital",
    "सायन अस्पताल": "Sion Hospital",
    "सायन रुग्णालय": "Sion Hospital",
    "वडाळा": "Wadala",
    "धारावी": "Dharavi",
    "प्रभादेवी": "Prabhadevi",
    "खार": "Khar",
    "सांताक्रूझ": "Santacruz",
    "सांताक्रूज": "Santacruz",
    "सांताक्रूज़": "Santacruz",
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
    "बाहेरील", "समोरील", "मागील", "जवळील",
    "च्या", "जवळ", "बाहेर", "समोर", "मागे", "मध्ये", "खाली",
    "वर", "ात", "आत",
    "javal", "baher", "samor",
]

MARATHI_OBLIQUE_MAP = {
    "स्थानका": "स्थानक",
    "बाजारा": "बाजार",
    "रुग्णालया": "रुग्णालय",
    "प्रवेशद्वारा": "प्रवेशद्वार",
    "प्रवेशद्वाराच्या": "प्रवेशद्वार",
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

    # Marathi commonly places a locality before its landmark with "येथील"
    # (for example, "[locality] येथील [college]"). Keep both pieces, with the
    # landmark first so the geocoder can resolve the specific place in context.
    locality_place = re.match(r"^(.+?)\s+येथील\s+(.+)$", cleaned)
    if locality_place:
        locality, place = locality_place.groups()
        cleaned = f"{place.strip()}, {locality.strip()}"

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

    cleaned = _clean_candidate(cleaned)
    # Postposition normalization can expose a generic boundary word such as
    # प्रवेशद्वार from प्रवेशद्वाराबाहेर; remove that after normalization too.
    cleaned = _strip_problem_words_from_phrase(cleaned)
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


def _strip_social_handles(text):
    """Remove source-account tags such as @mybmc before extracting place names."""
    return re.sub(r"(?<!\w)@[A-Za-z0-9_]+", " ", str(text or ""))


def _extract_feature_phrase_candidates(text):
    """Extract named-place phrases based on place-feature words when no connector exists."""
    candidates = []
    norm = re.sub(r"\s+", " ", _strip_social_handles(text)).strip()
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
    normalized = re.sub(r"\s+", " ", _strip_social_handles(text)).strip()
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
    normalized = re.sub(r"[^\w\s]", " ", (value or "").casefold())
    return _compact_initials(normalized)


def _compact_initials(value):
    """Normalize dotted or spaced Latin initials generically (for example J.B. -> JB)."""
    return re.sub(
        r"(?<!\w)(?:[a-z]\.\s*){2,}(?=\s|$)|(?<!\w)(?:[a-z]\s+){1,}[a-z](?=\s|$)",
        lambda match: re.sub(r"[\s.]", "", match.group(0)),
        str(value or ""),
        flags=re.IGNORECASE,
    )


def _replace_location_aliases(value):
    aliases = sorted(CANONICAL_LOCATION_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)
    lookup = {alias.casefold(): replacement for alias, replacement in aliases}
    patterns = []
    for alias, _replacement in aliases:
        escaped = re.escape(alias)
        if alias.isascii():
            patterns.append(r"(?<!\w)" + escaped + r"(?!\w)")
        else:
            # Marathi/Hindi often attach grammatical endings directly to the
            # locality name (e.g. मालाडमधील, कुर्ल्यातील). Consume the ending
            # along with the name so normalization yields a clean place label.
            suffix = r"(?:यामधील|यामध्ये|यातील|मधील|मधले|मध्ये|मधून|च्या|चा|ची|चे|तील|कडील|जवळील)?"
            patterns.append(r"(?<!\w)" + escaped + suffix + r"(?!\w)")
    if not patterns:
        return value
    matcher = re.compile("|".join(patterns), re.IGNORECASE)
    def replace_alias(match):
        matched = match.group(0).casefold()
        replacement = lookup.get(matched)
        if replacement:
            return replacement
        return next(
            (canonical for alias, canonical in aliases if matched.startswith(alias.casefold())),
            match.group(0),
        )
    return matcher.sub(replace_alias, value)


def _canonical_query(candidate):
    """Translate known Devanagari/local place aliases to canonical English before search."""
    canonical = _clean_candidate(candidate)
    if not canonical:
        return None
    canonical = _compact_initials(canonical)
    canonical = _replace_location_aliases(canonical)
    return _clean_candidate(canonical)


_CLUSTER_ISSUE_ALIASES = {
    "pothole": ("pothole", "potholes", "खड्डा", "खड्डे", "खड्ड्यात", "गड्ढा", "गड्ढे", "गड्ढों", "gaddha", "gaddhe", "khadda", "khadde"),
    "road": ("road", "roads", "roadway", "सड़क", "सड़कें", "रस्ता", "रस्त्यावर", "रस्त्याचा", "रस्त्याची", "रस्त्याचे", "sadak", "sadak par", "rasta", "raasta", "rastyavar"),
    "road damage": ("damaged road", "bad road", "poor road condition", "bad conditions of road", "road condition is bad", "road is in bad condition", "pathetic condition of the road", "pathetic condition road", "road ki halat bahot kharab", "road ki halat bahut kharab", "रस्त्याची दुरावस्था", "रस्त्याची दूरवस्था", "रस्त्याची अवस्था खराब", "रस्त्याची दयनीय अवस्था", "रस्त्याची दयनीय स्थिती", "रस्त्याची खराब अवस्था", "सड़क की हालत बहुत खराब", "सड़क की हालत खराब", "सड़क की खराब हालत", "सड़क की स्थिति खराब", "सड़क की दयनीय स्थिति", "सड़क की खराब स्थिति", "रोड की हालत बहुत खराब"),
    "muddy road": ("muddy road", "mud covered road", "कीचड़", "चिखल", "keechad", "kichad", "chikhal"),
    "garbage": ("garbage", "rubbish", "waste", "कचरा", "कचऱ्याचा", "कचऱ्याची"),
    "water leakage": ("water leak", "leakage", "पाणी गळती", "पाण्याची गळती", "पाइपलाइन", "पाईपलाईन"),
    "pipe burst": (
        "pipe burst", "pipeline burst", "burst pipe", "burst pipeline", "pipe has burst",
        "pipeline has burst", "pipe broke", "pipeline broke", "broken pipeline",
        "पाइपलाइन फट", "पाइपलाइन फुट", "पाइपलाइन टूट", "पाइपलाईन फुट", "पाईपलाईन फुट", "पाईपलाईन फाट",
        "पाईप फुट", "नळाची पाइपलाइन फुट", "nal futli", "nalachi line futli",
        "pipeline burst zali", "pipeline futli",
    ),
    "footpath": ("footpath", "footpaths", "pavement", "sidewalk", "पदपथ", "फुटपाथ", "पादचारी मार्ग", "padpath", "phutpath"),
    "footpath damage": (
        "broken footpath", "footpath is broken", "damaged footpath", "footpath damage", "pavement is broken",
        "पदपथ तुटला", "पदपथ तुटलेला", "फुटपाथ तुटला", "फुटपाथ तुटले", "फुटपाथ तुटलेला",
        "फुटपाथ तुटलेला असल्याने", "फुटपाथ टूटा", "टूटा हुआ फुटपाथ", "फुटपाथ टूट गया",
        "फुटपाथ टूटने", "टूटे हुए फुटपाथ", "padpath tutla", "footpath tuta",
    ),
    "footpath encroachment": ("footpath encroachment", "footpath encroached", "encroached footpath", "footpath occupied", "hawkers on the footpath", "फुटपाथ पर अतिक्रमण", "पदपथावर अतिक्रमण", "पदपथ अडवला", "पदपथ अडवले", "फुटपाथ अडवला"),
    "encroachment": ("encroachment", "encroachments", "encroached", "encroaching", "hawker encroachment", "illegal hawkers", "illegal food stalls", "unauthorized vendors", "अनधिकृत फेरीवाले", "अवैध फेरीवाले", "illegal parking", "unauthorized parking", "unlawful parking", "अतिक्रमण", "अतिक्रमणे", "अतिक्रमणाचा", "अतिक्रमणामुळे", "अवैध कब्जा", "अनधिकृत अतिक्रमण", "बेकायदेशीर पार्किंग", "अनधिकृत पार्किंग", "अवैध पार्किंग", "पदपथ अडवले", "पदपथ अडवत", "फुटपाथ अडवले", "atikraman", "anadhikrut atikraman", "avaidh kabja"),
    "illegal transport stand": (
        "illegal rickshaw stand", "unauthorized rickshaw stand", "unlicensed rickshaw stand",
        "illegal auto stand", "unauthorized auto stand", "rickshaw stand", "auto rickshaw stand",
        "अवैध रिक्शा स्टैंड", "अवैध रिक्शा स्टँड", "अनधिकृत रिक्शा स्टैंड", "अनधिकृत रिक्शा स्टँड",
        "बेकायदेशीर रिक्षा स्टँड", "बेकायदेशीर रिक्षा स्टॅण्ड", "अनधिकृत रिक्षा स्टँड",
        "अवैध रिक्षा स्टँड", "रिक्षा स्टँड", "रिक्शा स्टैंड",
    ),
}


def cluster_issue_concepts(text):
    """Extract conservative, language-independent issue tags for cluster safety."""
    normalized = _replace_location_aliases(text or "")
    lowered = normalized.casefold()
    original_lowered = (text or "").casefold()
    concepts = set()
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
            concepts.add(concept)
    return concepts


def normalize_cluster_text(text, *, include_issue_tags=False):
    """Canonicalize known local place names and optionally add safe issue tags.

    This is used only to build clustering embeddings. The original complaint,
    severity input, visible text, and stored location are left untouched.
    """
    normalized = _replace_location_aliases(text or "")

    if include_issue_tags:
        concepts = cluster_issue_concepts(text)
        # Preserve the historical tag order so existing embedding behavior
        # remains as stable as possible while the vocabulary expands.
        tags = [concept for concept in _CLUSTER_ISSUE_ALIASES if concept in concepts]
        if tags:
            normalized += "\nCanonical issue: " + ", ".join(tags)
    return normalized


def cluster_place_identity(text):
    """Return a canonical named-place hint for preventing nearby-place merges."""
    candidates = _extract_location_candidates(text or "")
    locality_tokens = set()
    for locality, _ward in LOCALITY_TO_WARD:
        canonical_locality = _canonical_query(locality) or locality
        locality_tokens.update(_normalise_for_match(canonical_locality).split())
    directions = {"east", "west", "north", "south", "central"}
    for candidate in candidates:
        canonical = _canonical_query(candidate)
        if not canonical:
            continue
        candidate_tokens = _normalise_for_match(canonical).split()
        has_place_feature = any(token in PLACE_FEATURE_WORDS for token in candidate_tokens)
        tokens = [
            token for token in candidate_tokens
            if token not in STOP_WORDS
            and token not in GENERIC_QUERY_WORDS
            and token not in PLACE_FEATURE_WORDS
            and token not in directions
            and len(token) > 1
        ]
        if has_place_feature:
            distinctive = [token for token in tokens if token not in locality_tokens]
            # Preserve a locality name when it is itself the only distinctive
            # part of a typed place such as Versova Metro Station.
            tokens = distinctive or tokens
        elif tokens and all(token in locality_tokens for token in tokens):
            continue
        if tokens:
            return " ".join(tokens)
    return ""


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
        logger.info("LOCATION_STAGE provider=nominatim outcome=error reason=request_or_malformed_response error_type=%s", type(error).__name__)
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
        logger.info("LOCATION_STAGE provider=nominatim outcome=no_result")
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
        logger.info("LOCATION_STAGE provider=nominatim outcome=accepted")
        matched = {
            "lat": lat,
            "lng": lng,
            "display_name": display_name or query,
            "canonical_name": canonical_query,
            "name": result.get("name"),
            "address": result.get("address") if isinstance(result.get("address"), dict) else {},
            "result_class": result.get("class"),
            "result_type": result.get("type"),
        }
        with _nominatim_lock:
            _nominatim_cache[canonical_query] = dict(matched)
        return matched

    with _nominatim_lock:
        _nominatim_cache[canonical_query] = None
    logger.info("LOCATION_STAGE provider=nominatim outcome=rejected reason=no_candidate_passed_validation")
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

    # Do not resolve a generic suffix such as "metro station" to whichever
    # station happens to rank first. A named landmark must contribute at least
    # one distinctive query token that also appears in the provider feature.
    if not distinctive_terms:
        return False, 0.0, "missing_distinctive_query_terms"

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

    requested_type = _location_feature_from_text(candidate)
    if requested_type and not _location_type_matches(
        requested_type, r_type, r_cat,
        " ".join((r_name, r_formatted, r_type, r_cat)),
    ):
        return False, 0.0, "requested_feature_subtype_mismatch"

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
        logger.info("LOCATION_STAGE provider=geoapify outcome=skipped reason=missing_configuration")
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
        logger.info("LOCATION_STAGE provider=geoapify outcome=error reason=request_or_malformed_response error_type=%s", type(error).__name__)
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
        logger.info("LOCATION_STAGE provider=geoapify outcome=accepted")
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
        logger.info("LOCATION_STAGE provider=geoapify outcome=no_result")
        with _geoapify_lock:
            _geoapify_cache[canonical] = None
    elif results is not None:
        logger.info("LOCATION_STAGE provider=geoapify outcome=rejected reason=no_candidate_passed_validation result_count=%s", len(results))
    return None


def _fallback_result_matches_original_candidate(candidate, result):
    """Prevent progressively broader queries from losing the original landmark."""
    raw_result = {
        "lat": result.get("lat"),
        "lon": result.get("lng"),
        "name": result.get("returned_name") or result.get("name") or "",
        "formatted": result.get("formatted_address") or result.get("display_name") or "",
        "result_type": result.get("result_type") or result.get("type") or "",
        "category": result.get("category") or result.get("result_class") or result.get("class") or "",
    }
    canonical = _canonical_query(candidate) or candidate
    accepted, _score, reason = _evaluate_geoapify_result(candidate, canonical, raw_result)
    if not accepted:
        logger.info(
            "LOCATION_FALLBACK outcome=rejected reason=original_candidate_mismatch "
            "validation_reason=%s requested=%r candidate_name=%r candidate_address=%r",
            reason, _diagnostic_field(canonical),
            _diagnostic_field(raw_result.get("name")),
            _diagnostic_field(raw_result.get("formatted")),
        )
    return accepted


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
            if not _fallback_result_matches_original_candidate(candidate, result):
                continue
            result["candidate_attempted"] = variant
            return result

        result = _nominatim_search(variant, timeout=min(timeout, 5))
        if result:
            result["source"] = "nominatim"
            if not _fallback_result_matches_original_candidate(candidate, result):
                continue
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
_LOCATION_AI_SYSTEM_PROMPT = """Extract ONLY actual place entities from a civic complaint in English, Hindi, Marathi, romanized Hindi/Marathi, or mixed language. Return the schema exactly. Keep the original text in mention. Set normalized_name to the concise English or Latin-script common/official name used by geocoders; when the place is written in Devanagari, transliterate its proper name into Latin script rather than returning Devanagari. Put the generic feature (college, school, station, etc.) in location_type and preserve it in normalized_name only if it is part of the commonly used place name. A primary specific entity must remain primary; a surrounding locality belongs in context and must never replace it. If a distinctive named place has an unambiguous known locality, include that locality as context even when the complaint omits it; the geocoder must independently verify both the place and locality before coordinates can be accepted. Do not guess the locality when uncertain. A name may span multiple adjacent words/entities: do not split a proper name into separate locality/endpoints. A report describing a path, road, or route toward a place is about one target location, not a report between endpoints. Mark is_between true only when the text explicitly says the issue is between two places (including equivalent Hindi, Marathi, and Romanized wording). For a between complaint return exactly two endpoint entities with roles endpoint_a and endpoint_b. Do not invent names. A generic unnamed category such as 'a hospital', 'the market', or 'an unnamed road' is not a resolvable named place: use has_location false unless a named locality is explicitly present, in which case extract only that named locality. Use a broad, closest location_type, not a fixed vocabulary. confidence is 0 through 1."""


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
        logger.info("LOCATION_STAGE provider=groq outcome=skipped reason=empty_input_or_missing_configuration")
        logger.debug("LOCATION AI unavailable reason=%s", "empty_text_or_missing_client_or_key")
        return None
    cache_key = text.strip()
    with _location_ai_lock:
        cached = _location_ai_cache.get(cache_key)
    if cached is not None:
        logger.info("LOCATION_STAGE provider=groq outcome=cache_hit location_count=%s", len(cached.get("locations", [])))
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
        logger.info("LOCATION_STAGE provider=groq outcome=error reason=timeout")
        logger.debug("LOCATION AI ERROR provider=groq type=timeout")
        return None
    except APIConnectionError:
        logger.info("LOCATION_STAGE provider=groq outcome=error reason=connection_error")
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
        logger.info("LOCATION_STAGE provider=groq outcome=error reason=%s status=%s", error_type, status_code)
        logger.debug("LOCATION AI ERROR provider=groq status=%s type=%s", status_code, error_type)
        return None
    except Exception:  # Provider errors must never reject a complaint.
        logger.info("LOCATION_STAGE provider=groq outcome=error reason=unexpected_error")
        logger.debug("LOCATION AI ERROR provider=groq type=unexpected_error")
        return None

    logger.debug("LOCATION AI SUCCESS provider=groq model=openai/gpt-oss-20b status=200")
    try:
        content = response.choices[0].message.content
        extracted = _valid_location_ai_payload(json.loads(content))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        logger.info("LOCATION_STAGE provider=groq outcome=error reason=malformed_structured_response")
        logger.debug("LOCATION AI ERROR provider=groq type=malformed_structured_response")
        return None
    if extracted is None:
        logger.info("LOCATION_STAGE provider=groq outcome=error reason=invalid_structured_response")
        logger.debug("LOCATION AI ERROR provider=groq type=malformed_structured_response")
        return None
    logger.debug("LOCATION AI extracted location_count=%s", len(extracted["locations"]))
    logger.info("LOCATION_STAGE provider=groq outcome=extracted location_count=%s has_location=%s is_between=%s", len(extracted["locations"]), extracted["has_location"], extracted["is_between"])
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
    tokens = re.findall(r"\w+", _normalise_for_match(value), flags=re.UNICODE)
    normalized = set()
    for token in tokens:
        if token.isascii():
            # Common romanization variation in Indian proper names: long
            # vowels and aspirated/f sounds vary across Hindi/Marathi typing.
            token = re.sub(r"ee$", "i", token)
            token = re.sub(r"oo$", "u", token)
            token = re.sub(r"aa$", "a", token)
            token = token.replace("ph", "f")
        if (token not in STOP_WORDS and token not in GENERIC_QUERY_WORDS
                and token not in PLACE_FEATURE_WORDS and len(token) > 1):
            normalized.add(token)
    return normalized


def _entity_name_aliases(entity):
    """Return the canonical extracted name and source mention as matchable aliases."""
    aliases = []
    for value in (entity.get("normalized_name"), entity.get("mention")):
        value = _canonical_query(value or "")
        if value and _normalise_for_match(value) not in {_normalise_for_match(item) for item in aliases}:
            aliases.append(value)
    return aliases


def _structured_query_variants(entity):
    """Try contextual searches first, then broader searches with locality validation."""
    contextual_variants = []
    broad_variants = []
    query_names = []
    for name in _entity_name_aliases(entity):
        # Geocoders often index a station or institution by its distinctive
        # name and omit the generic feature suffix ("Versova" rather than
        # "Versova Metro Station"). Search progressively broader name forms,
        # but retain the original entity and type for candidate validation.
        for query_name in _candidate_variants(name)[:2]:
            normalized_query_name = _normalise_for_match(query_name)
            if normalized_query_name not in {
                _normalise_for_match(existing) for existing in query_names
            }:
                query_names.append(query_name)
    for name in query_names:
        named_entity = {**entity, "normalized_name": name}
        contextual_query = _structured_query(named_entity)
        if contextual_query and contextual_query not in contextual_variants:
            contextual_variants.append(contextual_query)
    # Retain a single broad query for each model-provided alias. This preserves
    # the old fallback coverage without multiplying geocoder requests for every
    # shortened feature-name variant.
    for name in _entity_name_aliases(entity):
        broad_query = _structured_query({**entity, "normalized_name": name}, include_context=False)
        if broad_query and broad_query not in broad_variants:
            broad_variants.append(broad_query)
    parent_variants = []
    locality = _known_locality_context(entity.get("context", ""))
    parent = LOCALITY_PARENT_CONTEXT.get(locality or "")
    if parent:
        for name in query_names[:2]:
            query = ", ".join((name, locality, parent, "Mumbai", "Maharashtra", "India"))
            if query not in contextual_variants and query not in parent_variants:
                parent_variants.append(query)
    ordered = contextual_variants + parent_variants + broad_variants
    return list(dict.fromkeys(ordered))[:5]


def _best_entity_match(entity, result_tokens):
    matches = []
    for alias in _entity_name_aliases(entity):
        tokens = _entity_tokens(alias)
        if tokens:
            matches.append((len(tokens & result_tokens) / len(tokens), alias))
    return max(matches, default=(0.0, ""), key=lambda item: item[0])


def _primary_entity_overlap(entity, result_tokens, result_name=""):
    """Match a distinctive entity, including exact names made only of feature words.

    Token scoring deliberately drops generic feature terms (like "station" or
    "hospital"). That can leave no identity tokens for real named places such
    as Masjid Railway Station. An exact provider name is strong identity
    evidence, but locality and feature-type checks still apply separately.
    """
    overlap, alias = _best_entity_match(entity, result_tokens)
    provider_name = _normalise_for_match(result_name)
    if provider_name:
        for requested_alias in _entity_name_aliases(entity):
            requested = _normalise_for_match(requested_alias)
            if requested and requested == provider_name:
                return 1.0, requested_alias
    return overlap, alias


def _context_overlap(context, metadata):
    context_tokens = _entity_tokens(context)
    if not context_tokens:
        return 1.0
    result_tokens = _entity_tokens(metadata)
    return len(context_tokens & result_tokens) / len(context_tokens)


def _postcode_confirms_context(context, result):
    locality = _known_locality_context(context)
    postcode = re.sub(r"\D", "", str(result.get("postcode") or ""))
    accepted_postcodes = LOCALITY_POSTCODES.get(locality or "", set())
    return bool(postcode and postcode in accepted_postcodes)


def _diagnostic_field(value, limit=120):
    """Bound and single-line a location field before writing it to diagnostics."""
    return re.sub(r"[\r\n\t]+", " ", str(value or ""))[:limit]


def _location_type_matches(entity_type, result_type, category, metadata):
    """Broad type compatibility, deliberately generic rather than landmark rules."""
    requested = _normalise_for_match(entity_type).replace("_", " ")
    returned = _normalise_for_match(" ".join([result_type or "", category or "", metadata or ""]).replace("_", " "))
    feature_identity = _normalise_for_match(metadata or "").replace("_", " ")
    # A generic "station" match must not override a known, incompatible
    # transport/facility subtype (for example Versova Police station for
    # Versova Metro Station, or a metro stop for a railway station).
    transit_request = requested in {"railway station", "metro station", "station", "transit stop"}
    if transit_request and re.search(r"\bpolice\b", returned):
        return False
    # In Mumbai, an unqualified named "station" means the railway station in
    # ordinary civic reports. Bus facilities are requested explicitly and
    # have their own types; do not accept a bus station as a loose substitute.
    if requested in {"station", "transit stop"} and re.search(
        r"\bbus\s+(?:station|terminal|stop)\b", feature_identity
    ):
        return False
    if requested == "metro station":
        # OSM/geocoders sometimes classify metro platforms under the generic
        # railway/station taxonomy. Reject only an explicitly named rail or bus
        # station; a generic railway category must not hide a matching metro POI.
        explicitly_metro = bool(re.search(r"\b(?:metro|subway)\b", feature_identity))
        explicitly_other_station = bool(re.search(
            r"\b(?:railway|railroad|train)\s+station\b|\bbus\s+(?:station|stop|terminal)\b",
            feature_identity,
        ))
        if explicitly_other_station and not explicitly_metro:
            return False
    elif requested == "railway station":
        # As above, classify from a named feature phrase, not a broad provider
        # category such as `railway`, which can also contain metro platforms.
        explicitly_railway = bool(re.search(r"\b(?:railway|railroad|train)\s+station\b", feature_identity))
        explicitly_other_station = bool(re.search(r"\b(?:metro|subway)\s+station\b|\bbus\s+(?:station|stop|terminal)\b", feature_identity))
        if explicitly_other_station and not explicitly_railway:
            return False
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
        "police": ("police",), "police station": ("police",), "police chowky": ("police",),
        "fire station": ("fire station", "fire brigade", "fire_service"),
        "post office": ("post office", "post_office"),
        "bank": ("bank",), "atm": ("atm", "bank"),
        "library": ("library",), "museum": ("museum",),
        "government office": ("government", "municipal", "public office"),
        "municipal office": ("municipal", "government", "public office"),
        "airport": ("airport", "aerodrome"), "bus station": ("bus station", "bus terminal", "bus_stop"),
        "bus stop": ("bus stop", "bus_station", "transit"),
        "stadium": ("stadium", "sports"), "playground": ("playground", "sports", "recreation"),
        "court": ("court", "courthouse"), "place of worship": ("church", "temple", "mosque", "worship", "gurudwara"),
    }
    options = family.get(requested)
    return True if not options or requested in {"unknown", "landmark", "place"} else any(option in returned for option in options)


def _resolved_result_matches_entity(entity, result):
    """Recheck loose fallback results against the original extracted place."""
    if not entity or not isinstance(result, dict):
        return False
    address = result.get("formatted_address") or result.get("display_name") or ""
    fallback_candidate = {
        "lat": result.get("lat"),
        "lon": result.get("lng"),
        "name": result.get("returned_name") or result.get("name") or "",
        "formatted": address,
        "result_type": result.get("result_type") or result.get("type") or "",
        "category": result.get("category") or result.get("result_class") or result.get("class") or "",
        "rank": {"confidence": 0.5},
    }
    accepted, _score, reason = _score_structured_geoapify(entity, fallback_candidate)
    if not accepted:
        logger.info(
            "LOCATION_FALLBACK outcome=rejected reason=original_entity_mismatch "
            "requested_name=%r candidate_name=%r candidate_address=%r",
            _diagnostic_field(entity.get("normalized_name")),
            _diagnostic_field(fallback_candidate.get("name")),
            _diagnostic_field(address),
        )
    return accepted


def _geoapify_candidate_metadata(result):
    """Include provider address components when validating locality context."""
    address_fields = (
        "address_line1", "address_line2", "street", "suburb", "neighbourhood",
        "city", "city_district", "district", "county", "municipality", "state",
        "postcode", "country",
    )
    return " ".join(str(result.get(key) or "") for key in (
        "name", "formatted", "category", "result_type", *address_fields,
    ))


def _geoapify_address_metadata(result):
    """Return locality evidence without letting the feature name satisfy it."""
    address_fields = (
        "suburb", "neighbourhood",
        "city", "city_district", "district", "county", "municipality", "state",
        "postcode", "country",
    )
    # Geoapify's formatted field starts with the POI name. Remove that first
    # component so "Ghatkopar Bus Station, Vikhroli" is not validated as being
    # in Ghatkopar merely because its name contains that word.
    formatted_parts = str(result.get("formatted") or "").split(",")
    address_text = ", ".join(formatted_parts[1:])
    return " ".join(
        [address_text]
        + [str(result.get(key) or "") for key in address_fields]
    )


def _provider_feature_name(name, formatted):
    """Get the provider's feature label without locality/address components."""
    name = str(name or "").strip()
    if name:
        return name
    return str(formatted or "").split(",", 1)[0].strip()


def _distance_km(lat1, lng1, lat2, lng2):
    radius_km = 6371.0
    lat1, lng1, lat2, lng2 = map(math.radians, (lat1, lng1, lat2, lng2))
    lat_delta, lng_delta = lat2 - lat1, lng2 - lng1
    value = math.sin(lat_delta / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(lng_delta / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(value))


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
    metadata = _geoapify_candidate_metadata(result)
    context = entity.get("context", "")
    # Identity must come from the returned feature name, not merely from a
    # matching word in its address (e.g. a different station whose address
    # mentions Versova). Address fields remain valid locality evidence.
    feature_name = _provider_feature_name(name, formatted)
    primary_overlap, matched_alias = _primary_entity_overlap(
        entity, _entity_tokens(feature_name), feature_name
    )
    primary_tokens = _entity_tokens(matched_alias)
    context_overlap = _context_overlap(context, _geoapify_address_metadata(result))
    if context.strip() and context_overlap < 0.70 and _postcode_confirms_context(context, result):
        context_overlap = 1.0
    # Address text helps validate locality but must not prove place type. A
    # nearby station in the address cannot make a beach a station result.
    # Include the provider's feature label (usually the first part of
    # `formatted`) when validating type. This catches explicit subtypes such as
    # "Dadar Police Station" even when Geoapify leaves its `name` blank.
    formatted_feature = str(formatted).split(",", 1)[0].strip()
    # Subtype conflicts must come from the named feature itself. Provider
    # categories can label metro platforms as railway stations generically.
    type_evidence = " ".join((feature_name, formatted_feature, name))
    # A feature type (college, station, school, etc.) is not identity evidence.
    # Require a strong match on the distinctive name so a different place of
    # the same type cannot inherit its coordinates from a partial token match.
    minimum_primary_overlap = 1.0 if len(primary_tokens) <= 2 else 0.75
    exact_named_result = bool(
        matched_alias
        and _normalise_for_match(matched_alias) == _normalise_for_match(name)
    )
    if (not primary_tokens and not exact_named_result) or primary_overlap < minimum_primary_overlap:
        return False, 0.0, "primary_entity_tokens_missing"
    if context.strip() and context_overlap < 0.70:
        return False, 0.0, "context_tokens_missing"
    if not _location_type_matches(entity.get("location_type", "unknown"), result_type, category, type_evidence):
        return False, 0.0, "requested_type_mismatch"
    rank = result.get("rank") if isinstance(result.get("rank"), dict) else {}
    try:
        confidence = float(rank.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    match_bonus = 0.1 if str(rank.get("match_type", "")).casefold() in {"full_match", "exact_match"} else 0.0
    full_name = _normalise_for_match(matched_alias)
    name_similarity = 1.0 if full_name and full_name in _normalise_for_match(name) else primary_overlap
    score = 0.42 * primary_overlap + 0.18 * context_overlap + 0.20 * name_similarity + 0.12 * max(0.0, min(confidence, 1.0)) + match_bonus
    return score >= 0.68, score, "accepted" if score >= 0.68 else "score_too_low"


def _geoapify_structured_search(entity, timeout, *, allow_locality_corroboration=True):
    api_key = os.getenv("GEOAPIFY_API_KEY")
    queries = _structured_query_variants(entity)
    if not queries or not api_key:
        logger.info("LOCATION_STAGE provider=geoapify_structured outcome=skipped reason=%s", "missing_configuration" if not api_key else "missing_place_entity")
        return None
    # Never discard explicit locality context during geocoder fallback.
    for query_index, query in enumerate(queries, start=1):
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
            logger.info("LOCATION_STAGE provider=geoapify_structured outcome=error reason=request_or_malformed_response error_type=%s", type(error).__name__)
            logger.debug("LOCATION GEOAPIFY failed error=%s", type(error).__name__)
            continue
        best, best_score = None, -1.0
        context_missing_candidates = []
        rejection_counts = {}
        for result in results:
            if not isinstance(result, dict):
                continue
            accepted, score, reason = _score_structured_geoapify(entity, result)
            if not accepted:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                if reason == "context_tokens_missing":
                    context_missing_candidates.append(result)
                metadata = _geoapify_candidate_metadata(result)
                requested_tokens = _entity_tokens(entity.get("context", ""))
                returned_tokens = _entity_tokens(metadata)
                matched_context = sorted(requested_tokens & returned_tokens)
                missing_context = sorted(requested_tokens - returned_tokens)
                primary_overlap, _matched_alias = _best_entity_match(entity, returned_tokens)
                logger.info(
                    "LOCATION_CANDIDATE provider=geoapify outcome=rejected query_variant=%s "
                    "reason=%s requested_name=%r requested_context=%r candidate_name=%r "
                    "matched_context_tokens=%s missing_context_tokens=%s primary_overlap=%.2f",
                    query_index, reason,
                    _diagnostic_field(entity.get("normalized_name")),
                    _diagnostic_field(entity.get("context")),
                    _diagnostic_field(result.get("name")),
                    ",".join(matched_context), ",".join(missing_context), primary_overlap,
                )
            logger.debug("LOCATION GEOAPIFY returned_name=%r returned_address=%r result_type=%r confidence=%r score=%.2f accepted=%s reason=%s", result.get("name"), result.get("formatted"), result.get("result_type"), (result.get("rank") or {}).get("confidence"), score, accepted, reason)
            if accepted and score > best_score:
                best, best_score = result, score
        if best:
            logger.info(
                "LOCATION_STAGE provider=geoapify_structured outcome=accepted "
                "result_count=%s query_variant=%s candidate_name=%r "
                "candidate_address=%r candidate_type=%r candidate_category=%r "
                "lat=%s lng=%s score=%.2f",
                len(results), query_index,
                _diagnostic_field(best.get("name")),
                _diagnostic_field(best.get("formatted")),
                _diagnostic_field(best.get("result_type")),
                _diagnostic_field(best.get("category")),
                best.get("lat"), best.get("lon"), best_score,
            )
            matched = {"lat": float(best["lat"]), "lng": float(best["lon"]), "display_name": best.get("formatted") or best.get("name"), "canonical_name": entity.get("normalized_name"), "source": "geoapify"}
            with _geoapify_lock:
                _geoapify_cache[cache_key] = dict(matched)
            return matched
        # Some geocoders identify an exact landmark but omit its locality from
        # the result address. In that case, corroborate the landmark's returned
        # coordinates against an independently geocoded known locality. The
        # locality point is only a validation anchor; the landmark coordinates
        # remain the returned coordinates. A different provider locality label
        # is accepted only when the exact landmark is tightly corroborated nearby.
        context = entity.get("context", "")
        expected_locality = _known_locality_context(context)
        if allow_locality_corroboration and expected_locality and context_missing_candidates:
            anchor_localities = [expected_locality]
            base_locality = re.sub(r"\s+(?:east|west|north|south|central)$", "", expected_locality, flags=re.IGNORECASE).strip()
            if base_locality and _normalise_for_match(base_locality) != _normalise_for_match(expected_locality):
                anchor_localities.append(base_locality)
            anchor = None
            anchor_locality_used = None
            for anchor_locality in anchor_localities:
                locality_entity = {
                    "normalized_name": anchor_locality,
                    "mention": anchor_locality,
                    "context": "",
                    "location_type": "locality",
                }
                anchor = _geoapify_structured_search(
                    locality_entity,
                    timeout,
                    allow_locality_corroboration=False,
                )
                if anchor:
                    anchor_locality_used = anchor_locality
                    break
                nominatim_anchor = _nominatim_search(anchor_locality, min(timeout, 5))
                if nominatim_anchor:
                    address = nominatim_anchor.get("address") if isinstance(nominatim_anchor.get("address"), dict) else {}
                    anchor_metadata = " ".join(str(value) for value in (
                        nominatim_anchor.get("display_name", ""), nominatim_anchor.get("name", ""), *address.values(),
                    ))
                    locality_type = str(nominatim_anchor.get("result_type") or "").casefold()
                    locality_class = str(nominatim_anchor.get("result_class") or "").casefold()
                    locality_types = {"administrative", "city_district", "neighbourhood", "quarter", "suburb", "town", "village"}
                    if (
                        _context_overlap(anchor_locality, anchor_metadata) >= 0.99
                        and _best_entity_match(locality_entity, _entity_tokens(anchor_metadata))[0] >= 1.0
                        and locality_type in locality_types
                        and locality_class in {"boundary", "place"}
                    ):
                        anchor = {"lat": nominatim_anchor.get("lat"), "lng": nominatim_anchor.get("lng")}
                        anchor_locality_used = anchor_locality
                        logger.info("LOCATION_STAGE provider=nominatim_locality outcome=accepted")
                        break
            if anchor and anchor.get("lat") is not None and anchor.get("lng") is not None:
                max_distance = (
                    MAX_LANDMARK_TO_LOCALITY_KM
                    if anchor_locality_used == expected_locality
                    else 1.5
                )
                best_nearby, best_distance = None, max_distance
                best_locality_mismatch = False
                corroboration_rejections = {}
                for candidate in context_missing_candidates:
                    metadata = _geoapify_candidate_metadata(candidate)
                    candidate_locality = _known_locality_context(metadata)
                    locality_mismatch = bool(candidate_locality and _normalise_for_match(candidate_locality) != _normalise_for_match(expected_locality))
                    without_context = {**entity, "context": ""}
                    primary_ok, score, _reason = _score_structured_geoapify(without_context, candidate)
                    candidate_name = _provider_feature_name(
                        candidate.get("name"), candidate.get("formatted")
                    )
                    candidate_identity_overlap = _primary_entity_overlap(
                        entity, _entity_tokens(candidate_name), candidate_name
                    )[0]
                    if not primary_ok or candidate_identity_overlap < 1.0:
                        corroboration_rejections["primary_entity_not_exact"] = corroboration_rejections.get("primary_entity_not_exact", 0) + 1
                        continue
                    try:
                        candidate_lat = float(candidate["lat"])
                        candidate_lng = float(candidate["lon"])
                        distance = _distance_km(candidate_lat, candidate_lng, float(anchor["lat"]), float(anchor["lng"]))
                    except (KeyError, TypeError, ValueError, OverflowError):
                        corroboration_rejections["invalid_candidate_coordinates"] = corroboration_rejections.get("invalid_candidate_coordinates", 0) + 1
                        continue
                    # A provider's neighboring-locality label can be tolerated
                    # at a boundary, but not across several kilometres where it
                    # could place a complaint in a different neighborhood.
                    candidate_max_distance = min(max_distance, 1.0) if locality_mismatch else max_distance
                    if distance <= candidate_max_distance and distance <= best_distance:
                        best_nearby, best_distance = candidate, distance
                        best_locality_mismatch = locality_mismatch
                    else:
                        reason = "locality_label_mismatch" if locality_mismatch else "outside_locality_radius"
                        corroboration_rejections[reason] = corroboration_rejections.get(reason, 0) + 1
                if best_nearby:
                    logger.info(
                        "LOCATION_STAGE provider=geoapify_locality outcome=corroborated "
                        "distance_km=%.2f max_distance_km=%.1f locality_label_mismatch=%s",
                        best_distance,
                        max_distance,
                        best_locality_mismatch,
                    )
                    place_name = entity.get("normalized_name") or entity.get("mention")
                    display_name = best_nearby.get("formatted") or best_nearby.get("name") or place_name
                    if _context_overlap(context, _geoapify_candidate_metadata(best_nearby)) < 0.70:
                        if best_locality_mismatch:
                            candidate_locality = _known_locality_context(_geoapify_candidate_metadata(best_nearby))
                            display_name = f"{place_name}, {candidate_locality} (near {expected_locality}), Mumbai"
                        else:
                            display_name = f"{display_name}, {expected_locality}, Mumbai"
                    matched = {
                        "lat": float(best_nearby["lat"]),
                        "lng": float(best_nearby["lon"]),
                        "display_name": display_name,
                        "canonical_name": f"{place_name}, {expected_locality}",
                        "source": "geoapify",
                    }
                    with _geoapify_lock:
                        _geoapify_cache[cache_key] = dict(matched)
                    return matched
                logger.info(
                    "LOCATION_STAGE provider=geoapify_locality outcome=rejected anchor=%s reasons=%s",
                    anchor_locality_used,
                    ",".join(f"{reason}:{count}" for reason, count in sorted(corroboration_rejections.items())) or "no_candidate_passed",
                )
        if results:
            logger.info("LOCATION_STAGE provider=geoapify_structured outcome=rejected result_count=%s reasons=%s", len(results), ",".join(f"{reason}:{count}" for reason, count in sorted(rejection_counts.items())))
        else:
            logger.info("LOCATION_STAGE provider=geoapify_structured outcome=no_result")
    return None


def _photon_structured_search(entity, timeout):
    """Search multilingual OSM place names, then apply the same strict validator.

    Photon is a bounded fallback for names omitted or poorly ranked by Geoapify.
    It never turns a locality/nearby feature into an entity match: candidates
    must still pass primary-name, locality-context, type and Mumbai-bound checks.
    """
    global _last_photon_request_time
    query_variants = _structured_query_variants(entity)
    if not query_variants or os.getenv("PHOTON_ENABLED", "1").strip().casefold() in {"0", "false", "no", "off"}:
        logger.info("LOCATION_STAGE provider=photon outcome=skipped reason=%s", "disabled" if query_variants else "missing_place_entity")
        return None

    # Most value comes from a contextual search. A single broader alias is
    # enough to handle providers that index only the landmark's short name,
    # without multiplying latency or public-service traffic on every complaint.
    queries = list(dict.fromkeys(query_variants[:2]))
    for query in queries:
        cache_key = "photon:" + query
        with _photon_lock:
            if cache_key in _photon_cache:
                cached = _photon_cache[cache_key]
                if cached:
                    return dict(cached)
                continue
            elapsed = time.monotonic() - _last_photon_request_time
            if elapsed < PHOTON_RATE_LIMIT_SECONDS:
                time.sleep(PHOTON_RATE_LIMIT_SECONDS - elapsed)
            _last_photon_request_time = time.monotonic()

        try:
            response = _photon_session.get(
                PHOTON_URL,
                params={"q": query, "lang": "en", "limit": GEOAPIFY_RESULT_LIMIT,
                        "lat": 19.0760, "lon": 72.8777},
                headers={"User-Agent": NOMINATIM_USER_AGENT},
                timeout=min(float(timeout), 4.0),
            )
            response.raise_for_status()
            payload = response.json()
            features = payload.get("features", []) if isinstance(payload, dict) else []
            if not isinstance(features, list):
                features = []
        except (requests.RequestException, ValueError, TypeError, OverflowError) as error:
            logger.info("LOCATION_STAGE provider=photon outcome=error reason=request_or_malformed_response error_type=%s", type(error).__name__)
            continue

        best = None
        best_score = -1.0
        rejected = {}
        for feature in features:
            if not isinstance(feature, dict):
                continue
            properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
            geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
            coordinates = geometry.get("coordinates") if isinstance(geometry.get("coordinates"), list) else []
            if len(coordinates) < 2:
                continue
            name = str(properties.get("name") or "")
            address_parts = [
                name, properties.get("housenumber"), properties.get("street"),
                properties.get("neighbourhood"), properties.get("suburb"),
                properties.get("locality"), properties.get("district"),
                properties.get("county"),
                properties.get("city"), properties.get("state"),
                properties.get("postcode"), properties.get("country"),
            ]
            candidate = {
                "lat": coordinates[1], "lon": coordinates[0], "name": name,
                "postcode": properties.get("postcode"),
                "formatted": ", ".join(str(part) for part in address_parts if part),
                "category": properties.get("osm_key") or properties.get("class") or "",
                "result_type": properties.get("osm_value") or properties.get("type") or "",
                "rank": {"confidence": properties.get("extent", 0.5)},
            }
            accepted, score, reason = _score_structured_geoapify(entity, candidate)
            if not accepted:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            if score > best_score:
                best, best_score = candidate, score

        if best:
            matched = {
                "lat": float(best["lat"]), "lng": float(best["lon"]),
                "display_name": best.get("formatted") or best.get("name"),
                "canonical_name": entity.get("normalized_name"), "source": "photon",
            }
            with _photon_lock:
                _photon_cache[cache_key] = dict(matched)
            logger.info("LOCATION_STAGE provider=photon outcome=accepted result_count=%s", len(features))
            return matched

        logger.info("LOCATION_STAGE provider=photon outcome=rejected result_count=%s reasons=%s", len(features), ",".join(f"{reason}:{count}" for reason, count in sorted(rejected.items())))
        with _photon_lock:
            _photon_cache[cache_key] = None
    return None


def _structured_nominatim_matches(entity, result):
    """Apply the same primary/context guard after Nominatim's legacy checks."""
    address = result.get("address") if isinstance(result.get("address"), dict) else {}
    metadata = " ".join(str(value) for value in (
        result.get("display_name", ""), result.get("name", ""), *address.values(),
    ))
    feature_name = _provider_feature_name(
        result.get("name"), result.get("display_name")
    )
    primary_overlap, matched_alias = _primary_entity_overlap(
        entity, _entity_tokens(feature_name), feature_name
    )
    primary_tokens = _entity_tokens(matched_alias)
    context = entity.get("context", "")
    context_overlap = _context_overlap(context, metadata)
    # Keep the address out of type evidence: only the returned feature itself
    # can satisfy an explicit requested type such as hospital or railway station.
    type_evidence = str(result.get("name") or "")
    type_ok = _location_type_matches(entity.get("location_type", "unknown"), result.get("type"), result.get("class"), type_evidence)
    exact_named_result = bool(
        matched_alias
        and _normalise_for_match(matched_alias) == _normalise_for_match(result.get("name") or "")
    )
    accepted = (bool(primary_tokens) or exact_named_result) and primary_overlap >= (1.0 if len(primary_tokens) <= 1 else 0.5) and (not context.strip() or context_overlap >= 0.70) and type_ok
    logger.debug("LOCATION NOMINATIM primary_overlap=%.2f context_overlap=%.2f accepted=%s", primary_overlap, context_overlap, accepted)
    return accepted


def _resolve_structured_entity(entity, timeout):
    logger.debug("LOCATION AI resolving type=%r relation=%r", entity.get("location_type"), entity.get("relation"))
    result = _geoapify_structured_search(entity, timeout)
    if result:
        return result
    result = _photon_structured_search(entity, timeout)
    if result:
        return result
    # Nominatim retains its existing Mumbai bounds, cache, rate limit, and result validation.
    for query in _structured_query_variants(entity):
        result = _nominatim_search(query, min(timeout, 5))
        if result and _structured_nominatim_matches(entity, result):
            result["source"] = "nominatim"
            logger.info("LOCATION_STAGE provider=nominatim_structured outcome=accepted")
            return result
        if result:
            logger.info("LOCATION_STAGE provider=nominatim_structured outcome=rejected reason=entity_context_mismatch")
            logger.debug("LOCATION NOMINATIM accepted_by_legacy_validation=false reason=structured_entity_mismatch")
        if result:
            continue
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


def _known_locality_context(value):
    """Extract only a known locality (plus a nearby cardinal direction) from text."""
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    if not value:
        return None
    # Compare canonical Latin aliases too, so known Devanagari localities
    # remain available as context for independently validated landmarks.
    value = _replace_location_aliases(value)
    for native_direction, latin_direction in (
        ("पश्चिमी", "West"), ("पश्चिम", "West"),
        ("पूर्वी", "East"), ("पूर्व", "East"),
        ("उत्तरी", "North"), ("उत्तर", "North"),
        ("दक्षिणी", "South"), ("दक्षिण", "South"),
        ("वेस्ट", "West"), ("ईस्ट", "East"),
        ("नॉर्थ", "North"), ("साउथ", "South"),
    ):
        value = re.sub(r"(?<!\w)" + native_direction + r"(?!\w)", latin_direction, value)
    for locality, _ward in sorted(LOCALITY_TO_WARD, key=lambda item: len(item[0]), reverse=True):
        match = _keyword_matches(value.casefold(), locality.casefold())
        if not match:
            continue
        context = locality
        if isinstance(match, re.Match):
            remainder = value[match.end():]
            direction = re.match(r"\s+(east|west|north|south|central)\b", remainder, flags=re.IGNORECASE)
            if direction:
                context += " " + direction.group(1).title()
        return (_canonical_query(context) or context).title()
    return None


def _retain_complaint_locality(entity, text, context_entities=()):
    """Use a short, recognized locality; discard model context that is complaint prose."""
    candidates = [entity.get("context", "")]
    candidates.extend(item.get("normalized_name", "") for item in context_entities)
    candidates.append(text or "")
    for candidate in candidates:
        locality = _known_locality_context(candidate)
        if locality:
            updated = {**entity, "context": locality}
            # Models sometimes include "Mittal College Malad West" as one
            # primary name instead of separating the locality. Move only a
            # recognized locality that is also present in the complaint into
            # context, leaving the landmark identity intact for geocoding.
            for field in ("normalized_name", "mention"):
                name = str(updated.get(field) or "")
                if not name:
                    continue
                canonical_name = _replace_location_aliases(name)
                for native_direction, latin_direction in (
                    ("वेस्ट", "West"), ("ईस्ट", "East"),
                    ("नॉर्थ", "North"), ("साउथ", "South"),
                    ("पश्चिमी", "West"), ("पश्चिम", "West"),
                    ("पूर्वी", "East"), ("पूर्व", "East"),
                    ("उत्तरी", "North"), ("उत्तर", "North"),
                    ("दक्षिणी", "South"), ("दक्षिण", "South"),
                ):
                    canonical_name = re.sub(r"(?<!\w)" + native_direction + r"(?!\w)", latin_direction, canonical_name)
                locality_pattern = r"[\s,.-]+".join(re.escape(part) for part in locality.split())
                stripped = re.sub(
                    r"(?<!\w)" + locality_pattern + r"(?!\w)",
                    " ", canonical_name, flags=re.IGNORECASE,
                )
                stripped = _clean_candidate(stripped)
                if stripped and _entity_tokens(stripped):
                    updated[field] = stripped
            return updated
    return {**entity, "context": ""}


def _has_explicit_between_relation(text):
    """Only calculate a midpoint when the complaint text states a between relation."""
    return any(pattern.search(text or "") for pattern in BETWEEN_PATTERNS)


def _location_feature_from_text(text):
    """Return a generic place type found in the complaint, including Devanagari terms."""
    feature_aliases = (
        # Match specific compound features before their generic suffixes.
        ("metro station", ("metro station", "मेट्रो स्टेशन", "मेट्रो स्थानक")),
        ("railway station", ("railway station", "रेल्वे स्थानक", "रेलवे स्टेशन", "रेलवे स्थानक")),
        ("police station", ("police station", "police chowky", "पोलीस स्टेशन", "पोलीस ठाणे", "पुलिस स्टेशन", "पुलिस थाना", "थाना")),
        ("fire station", ("fire station", "fire brigade", "अग्निशमन केंद्र", "अग्निशमन स्थानक")),
        ("post office", ("post office", "डाकघर", "टपाल कार्यालय")),
        ("bus station", ("bus station", "bus terminal", "बस स्थानक", "बस स्टेशन", "बस टर्मिनल")),
        ("bus stop", ("bus stop", "बस स्टॉप", "बस थांबा")),
        ("government office", ("government office", "municipal office", "महानगरपालिका कार्यालय", "पालिका कार्यालय")),
        ("college", ("college", "कॉलेज")),
        ("school", ("school", "शाळा", "स्कूल")),
        ("university", ("university", "विद्यापीठ")),
        ("institute", ("institute", "इन्स्टिट्यूट", "इंस्टीट्यूट")),
        ("hospital", ("hospital", "हॉस्पिटल", "रुग्णालय", "दवाखाना", "अस्पताल")),
        ("clinic", ("clinic", "दवाखाना", "क्लिनिक")),
        ("market", ("market", "मार्केट", "बाजार", "बाज़ार")),
        ("library", ("library", "ग्रंथालय", "पुस्तकालय")),
        ("museum", ("museum", "संग्रहालय")),
        ("bank", ("bank", "बँक", "बैंक")),
        ("airport", ("airport", "विमानतळ", "हवाई अड्डा")),
        ("stadium", ("stadium", "क्रीडांगण")),
        ("place of worship", ("temple", "mosque", "church", "gurudwara", "मंदिर", "मशीद", "मस्जिद", "चर्च", "गुरुद्वारा")),
        ("road", ("road", "रस्ता", "रस्त्यावर", "सड़क")),
        # Keep an unqualified station generic; callers cannot infer metro vs
        # railway from that word alone.
        ("transit stop", ("station", "स्थानक", "स्टेशन", "थांबा")),
    )
    lowered = (text or "").casefold()
    for location_type, aliases in feature_aliases:
        for alias in aliases:
            if alias.isascii():
                if re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", lowered):
                    return location_type
            elif alias in lowered:
                return location_type
    return ""


def _recover_single_directional_entity(extraction, text):
    """Repair a model's false between classification from the single place in text.

    Rule-based connector extraction preserves the full target phrase. Model names
    are retained as alternate search aliases, while a recognized locality stays
    validation context rather than being folded into the landmark identity.
    """
    candidates = _extract_location_candidates(text)
    if not candidates:
        return None

    locality = _known_locality_context(text) or ""
    locality_key = _normalise_for_match(locality)
    name_parts = []
    source_entities = extraction.get("locations", [])
    for item in source_entities:
        name = str(item.get("normalized_name") or item.get("mention") or "").strip()
        if not name:
            continue
        canonical_name = _normalise_for_match(_canonical_query(name) or name)
        if locality_key and canonical_name == locality_key:
            continue
        if canonical_name not in {_normalise_for_match(_canonical_query(value) or value) for value in name_parts}:
            name_parts.append(name)

    location_type = _location_feature_from_text(text)
    model_type = next((str(item.get("location_type") or "") for item in source_entities
                       if str(item.get("location_type") or "").casefold() not in {"", "town", "city", "locality", "suburb", "district"}), "")
    location_type = location_type or model_type or "unknown"
    mention_parts = name_parts[:]
    if location_type != "unknown" and _normalise_for_match(location_type) not in {
        _normalise_for_match(value) for value in mention_parts
    }:
        mention_parts.append(location_type)

    return {
        "normalized_name": candidates[0],
        "mention": " ".join(mention_parts) or candidates[0],
        "context": locality,
        "location_type": location_type,
        "role": "primary",
    }


def resolve_location(text, timeout=GEOAPIFY_TIMEOUT_SECONDS):
    """Text-only location resolution: Groq -> Geoapify -> Nominatim -> ward -> unknown."""
    # Social account mentions are provenance tags, not part of the place name.
    location_text = re.sub(r"\s+", " ", _strip_social_handles(text)).strip()
    extraction = _extract_locations_with_groq(location_text, timeout=min(timeout, 6))
    structured_between_endpoints = None
    has_structured_context = False
    malformed_relationship = False
    primary_entity = None
    if extraction and extraction["has_location"]:
        for entity_index, entity in enumerate(extraction["locations"], start=1):
            raw_context = str(entity.get("context") or "")
            logger.info(
                "LOCATION_ENTITY provider=groq index=%s name=%r context_chars=%s "
                "context_tokens=%s context_has_known_locality=%s type=%r role=%r",
                entity_index,
                _diagnostic_field(entity.get("normalized_name")),
                len(raw_context),
                len(_entity_tokens(raw_context)),
                bool(_known_locality_context(raw_context)),
                _diagnostic_field(entity.get("location_type")),
                _diagnostic_field(entity.get("role")),
            )
        explicit_between = _has_explicit_between_relation(location_text)
        if extraction["is_between"] and explicit_between:
            endpoints = sorted((_retain_complaint_locality(item, location_text) for item in extraction["locations"]), key=lambda item: 0 if item["role"] == "endpoint_a" else 1)
            has_structured_context = any(item.get("context", "").strip() for item in endpoints)
            first, second = (_resolve_structured_entity(item, timeout) for item in endpoints)
            if first and second:
                sources = {first["source"], second["source"]}
                midpoint_provider = next((provider for provider in ("geoapify", "photon", "nominatim") if provider in sources), "nominatim")
                source = f"{midpoint_provider}_midpoint"
                final = {"lat": (first["lat"] + second["lat"]) / 2, "lng": (first["lng"] + second["lng"]) / 2, "display_name": f"Approximate midpoint between {endpoints[0]['normalized_name']} and {endpoints[1]['normalized_name']}", "canonical_name": f"{endpoints[0]['normalized_name']} ↔ {endpoints[1]['normalized_name']}", "source": source}
                logger.debug("LOCATION FINAL source=%s coordinates_available=true", final["source"])
                logger.info("LOCATION_RESULT source=%s coordinates=approximate", final["source"])
                return final
            structured_between_endpoints = endpoints
        else:
            false_between = extraction["is_between"] and not explicit_between
            if false_between:
                primary = _recover_single_directional_entity(extraction, location_text)
                # Do not fall back to an unstructured single-name lookup after
                # malformed relationship extraction; an unresolved result is safer.
                has_structured_context = True
                malformed_relationship = True
                logger.info("LOCATION_STAGE provider=groq outcome=relationship_corrected reason=no_explicit_between_relation")
            else:
                primary = next((item for item in extraction["locations"] if item["role"] == "primary"), extraction["locations"][0])
            if primary:
                context_entities = [
                    item for item in extraction["locations"]
                    if item is not primary and item.get("role") in {"context", "locality"}
                ] if not false_between else []
                explicit_type = _location_feature_from_text(location_text)
                current_type = _normalise_for_match(primary.get("location_type", "unknown"))
                generic_types = {"", "unknown", "landmark", "place", "locality", "neighborhood", "neighbourhood", "suburb", "town", "city", "district"}
                type_conflicts_with_text = bool(explicit_type) and not _location_type_matches(
                    explicit_type, current_type, "", current_type,
                )
                if explicit_type and (current_type in generic_types or type_conflicts_with_text):
                    recovered = _recover_single_directional_entity(extraction, location_text)
                    if recovered:
                        primary = recovered
                        context_entities = []
                        has_structured_context = True
                        logger.info("LOCATION_STAGE provider=rules outcome=entity_refined reason=explicit_place_type")
                    else:
                        primary = {**primary, "location_type": explicit_type}
                if not false_between and any(
                    not _known_locality_context(
                        " ".join((str(item.get("normalized_name") or ""), str(item.get("context") or "")))
                    )
                    for item in context_entities
                ):
                    recovered = _recover_single_directional_entity(extraction, location_text)
                    if recovered:
                        primary = recovered
                        context_entities = []
                        false_between = True
                        has_structured_context = True
                        logger.info("LOCATION_STAGE provider=groq outcome=entity_recovered reason=unrecognized_context_fragment")
                if not false_between:
                    primary = _retain_complaint_locality(primary, location_text, context_entities)
                primary_entity = primary
                logger.info(
                    "LOCATION_CONTEXT provider=groq outcome=normalized name=%r context=%r context_entity_count=%s",
                    _diagnostic_field(primary.get("normalized_name")),
                    _diagnostic_field(primary.get("context")),
                    len(context_entities),
                )
                has_structured_context = bool(primary.get("context", "").strip()) or has_structured_context
                result = _resolve_structured_entity(primary, timeout)
                if result:
                    logger.debug("LOCATION FINAL source=%s coordinates_available=true", result["source"])
                    logger.info(
                        "LOCATION_RESULT source=%s coordinates=verified lat=%s lng=%s label=%r",
                        result["source"], result.get("lat"), result.get("lng"),
                        _diagnostic_field(result.get("display_name")),
                    )
                    return result

    # If model extraction found a place but its wording/type did not match the
    # provider index, retry candidates bounded directly from the complaint.
    # Each candidate still goes through the structured name, locality, type,
    # and Mumbai-bounds validators, so this broadens recovery without accepting
    # an unrelated nearby station (or POI).
    if primary_entity and not malformed_relationship:
        rule_locality = _known_locality_context(location_text) or primary_entity.get("context", "")
        rule_type = _location_feature_from_text(location_text) or primary_entity.get("location_type", "unknown")
        for candidate in _extract_location_candidates(location_text)[:3]:
            rule_entity = {
                "normalized_name": candidate,
                "mention": candidate,
                "context": rule_locality,
                "location_type": rule_type,
                "role": "primary",
            }
            result = _resolve_structured_entity(rule_entity, timeout)
            if result:
                logger.info(
                    "LOCATION_STAGE provider=rules_structured outcome=accepted candidate=%r type=%r",
                    _diagnostic_field(candidate), _diagnostic_field(rule_type),
                )
                logger.info(
                    "LOCATION_RESULT source=%s coordinates=verified resolution_path=rules_structured "
                    "lat=%s lng=%s label=%r",
                    result.get("source"), result.get("lat"), result.get("lng"),
                    _diagnostic_field(result.get("display_name")),
                )
                return result

    # If structured extraction found a location but providers could not validate
    # it, retry the bounded rule-based candidates with the complaint's recognized
    # locality attached. The same strict provider identity, type and Mumbai-area
    # checks still apply. Only malformed between-style relationships are skipped.
    if not malformed_relationship:
        # A recognized structured place is the identity source of truth. If
        # provider-specific lookup misses, permit a legacy rule-based fallback
        # only when its result independently matches that same entity. This
        # prevents a broad suffix such as "metro station" from resolving to a
        # different station elsewhere in Mumbai.
        structured_identity_available = bool(extraction and extraction["has_location"])
        if structured_identity_available and primary_entity is None:
            logger.info("LOCATION_STAGE provider=linguistic_fallback outcome=skipped reason=structured_entity_unavailable")
        locality_context = _known_locality_context(location_text)
        if not structured_identity_available or primary_entity is not None:
            for candidate in _extract_location_candidates(location_text):
                contextual_candidate = candidate
                if locality_context and locality_context.casefold() not in candidate.casefold():
                    contextual_candidate = f"{candidate}, {locality_context}"
                result = _resolve_candidate_variants(contextual_candidate, timeout=timeout)
                if result:
                    if structured_identity_available and not _resolved_result_matches_entity(primary_entity, result):
                        continue
                    logger.debug("LOCATION rule_based_fallback source=%s", result.get("source"))
                    logger.info(
                        "LOCATION_RESULT source=%s coordinates=verified resolution_path=linguistic_fallback lat=%s lng=%s label=%r",
                        result.get("source"), result.get("lat"), result.get("lng"),
                        _diagnostic_field(result.get("display_name")),
                    )
                    return result
    else:
        logger.debug("LOCATION rule_based_fallback_skipped reason=malformed_relationship")

    if structured_between_endpoints:
        first, second = (_structured_ward_endpoint(item) for item in structured_between_endpoints)
        if first and second:
            final = {"lat": None, "lng": None, "display_name": f"Approximate area between {structured_between_endpoints[0]['normalized_name']} and {structured_between_endpoints[1]['normalized_name']}; exact location unresolved", "canonical_name": f"{structured_between_endpoints[0]['normalized_name']} ↔ {structured_between_endpoints[1]['normalized_name']}", "source": "ward_fallback", "ward_code": first["ward_code"] if first["ward_code"] == second["ward_code"] else None}
            logger.debug("LOCATION FINAL source=%s coordinates_available=false", final["source"])
            logger.info("LOCATION_RESULT source=ward_fallback coordinates=unresolved")
            return final
    fallback = _ward_fallback_result(location_text)
    if fallback:
        logger.debug("LOCATION FINAL source=%s coordinates_available=false", fallback["source"])
        logger.info("LOCATION_RESULT source=ward_fallback coordinates=unresolved")
        return fallback
    logger.debug("LOCATION FINAL source=none coordinates_available=false")
    logger.info("LOCATION_RESULT source=none coordinates=unresolved")
    return {"lat": None, "lng": None, "display_name": None, "canonical_name": None, "source": "none"}
