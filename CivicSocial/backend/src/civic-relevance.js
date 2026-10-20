// Conservative phrase rules, intentionally easy to extend as reviewed examples accrue.
// This is a keyword classifier, not semantic understanding or a guarantee of issue detection.
const CIVIC_ISSUE_PATTERNS = [
  /\bpotholes?\b/i,
  /\bgarbage\b.{0,50}\b(?:collect(?:ion|ed)?|pickup|pick-up|overflow(?:ing)?|not removed|piles?|dump(?:ed|ing)?)\b|\b(?:uncollected|overflowing)\b.{0,24}\bgarbage\b|\btrash (?:collection|pickup|overflowing)\b|\bwaste collection\b/i,
  /\bwater (?:leak(?:age)?|pipeline leak)\b/i,
  /\bdrain(?:age)?\b.{0,36}\b(?:blocked|clogged|overflow(?:ing)?)\b|\b(?:blocked|clogged)\b.{0,36}\bdrain(?:age)?\b/i,
  /\bdamaged footpaths?\b|\bfootpaths?\b.{0,24}\b(?:damaged|broken|unsafe)\b/i,
  /\bstreet ?lights?\b.{0,24}\b(?:off|broken|not working|damaged)\b|\b(?:off|broken|not working)\b.{0,24}\bstreet ?lights?\b/i,
  /\bencroachment\b|\bunauthori[sz]ed encroachment\b/i,
  /\b(?:illegal|unauthori[sz]ed|unlicensed)\b.{0,32}\b(?:food )?(?:stalls?|street vendors?)\b|\b(?:food )?stalls?\b.{0,32}\b(?:illegal|unauthori[sz]ed|unlicensed)\b/i,
  /\b(?:food poisoning|foodborne illness|contaminated food|unsafe food|spoiled food)\b/i,
  /\b(?:food|water)\b.{0,36}\b(?:poisoning|contaminat(?:ed|ion)|unsafe|unhygienic)\b|\b(?:poisoning|contaminat(?:ed|ion)|unsafe|unhygienic)\b.{0,36}\b(?:food|water)\b/i,
  /\b(?:sewage|sewer)\b.{0,32}\b(?:leak(?:ing)?|overflow(?:ing)?|blocked|exposed)\b|\b(?:leak(?:ing)?|overflow(?:ing)?|blocked|exposed)\b.{0,32}\b(?:sewage|sewer)\b/i,
  /(?:नाली|गटार|नाला|ड्रेनेज).{0,30}\b(?:blocked|clogged|overflowing|leaking|broken)\b|\b(?:blocked|clogged|overflowing|leaking|broken)\b.{0,30}(?:नाली|गटार|नाला|ड्रेनेज)/i,
  /गड्ढा|गड्ढे|गड्ढों/,
  /खड्ड(?:ा|े|यां)?|रस्त्यावर खड्डे/i,
  /कचरा.{0,24}(?:जमा|उचल|फैला|ढेर|नहीं हट)|(?:जमा|फैला|ढेर).{0,24}कचरा|घनकचरा.{0,24}(?:उचल|जमा)/i,
  /पाणी.{0,20}(?:गळती|गळत|गळतो)|पाईप(?:लाइन)? गळती/i,
  /नाली.{0,20}(?:जाम|तुंबली|बंद)|गटार.{0,20}(?:जाम|तुंबले|बंद)|जलभराव/i,
  /फुटपाथ.{0,20}(?:टूटा|टूटी|खराब|टूट गया)|टूटा हुआ फुटपाथ/i,
  /स्ट्रीट ?लाइट.{0,20}(?:बंद|खराब|नहीं जल)|बंद.{0,20}स्ट्रीट ?लाइट/i,
  /अतिक्रमण|अनधिकृत कब्जा/i,
  /पाणी.{0,20}(?:गळती|गळत)|गळती.{0,20}पाणी/i,
  /नाला.{0,20}(?:तुंबला|भरला|बंद)|गटार.{0,20}(?:तुंबले|बंद)/i,
  /पदपथ.{0,20}(?:तुटलेला|खराब|धोकादायक)|फुटपाथ.{0,20}(?:तुटलेला|खराब)/i,
  /पथदिवे?.{0,20}(?:बंद|नादुरुस्त)|बंद.{0,20}पथदिवे?/i,
  /अतिक्रमण|अनधिकृत बांधकाम/i,
  /(?:अवैध|अनधिकृत|बेकायदेशीर).{0,24}(?:खाद्य )?(?:स्टॉल|ठेले|फेरीवाले)|(?:खाद्य )?(?:स्टॉल|ठेले|फेरीवाले).{0,24}(?:अवैध|अनधिकृत|बेकायदेशीर)/i,
  /(?:अन्न|खाद्य पदार्थ|खाना).{0,24}(?:विषबाधा|दूषित|असुरक्षित|खराब)|(?:विषबाधा|दूषित|असुरक्षित).{0,24}(?:अन्न|खाद्य पदार्थ|खाना)/i,
  /(?:दूषित|असुरक्षित|खराब).{0,24}(?:अन्न|खाद्य|खाना)|(?:अन्न|खाद्य|खाना).{0,24}(?:विषबाधा|दूषित|असुरक्षित)/i,
];

// High precision fallback for clear reports that Groq can occasionally miss,
// especially short Hindi/Marathi posts. These rules require an issue state,
// so an ordinary mention of a signal or waste is not enough to forward.
const CLEAR_CIVIC_FALLBACKS = [
  {
    category: "Transport and mobility",
    reason: "The post explicitly reports a malfunctioning traffic signal.",
    pattern: /(?:traffic|road|वाहतूक|ट्रैफिक|यातायात)\s*(?:signal|सिग्नल).{0,55}(?:not working|does not work|isn't working|broken|malfunction\w*|काम नहीं कर रहा|काम नहीं कर रही|काम नहीं करता|काम नहीं करती|खराब|बंद|नादुरुस्त|काम करत नाही|चालत नाही)|(?:not working|does not work|isn't working|broken|malfunction\w*|काम नहीं कर रहा|काम नहीं कर रही|काम नहीं करता|काम नहीं करती|खराब|बंद|नादुरुस्त|काम करत नाही|चालत नाही).{0,55}(?:traffic|road|वाहतूक|ट्रैफिक|यातायात)\s*(?:signal|सिग्नल)/iu,
  },
  {
    category: "Solid Waste Management (SWM)",
    reason: "The post explicitly reports garbage accumulation or an area filled with waste.",
    pattern: /(?:garbage|trash|rubbish|waste).{0,45}(?:piled|piling|accumulat\w*|overflow\w*|filled|cover\w*|strewn|not collected|not removed)|(?:piled|accumulat\w*|overflow\w*|filled|cover\w*|strewn).{0,45}(?:garbage|trash|rubbish|waste)|(?:कचरा|कचरे|कचऱ्या|कचऱ्याने).{0,45}(?:जमा|साचला|साचलेला|भरलेला|भरलेली|भरले आहे|भरी|भरा|भरे|भर गया|भर गई|भर गए|फैला|ढेर|उचलला नाही|उचललेला नाही)|(?:जमा|साचला|साचलेला|भरलेला|भरलेली|भरले आहे|भरी|भरा|भरे|भर गया|भर गई|भर गए|फैला|ढेर).{0,45}(?:कचरा|कचरे|कचऱ्या)|(?:garbage|trash|waste).{0,45}(?:कचरा|कचरे|कचऱ्या)|(?:कचरा|कचरे|कचऱ्या).{0,45}(?:garbage|trash|waste)/iu,
  },
  {
    category: "Solid Waste Management (SWM)",
    reason: "The post explicitly reports that garbage has not been collected.",
    pattern: /(?:garbage|trash|rubbish|waste).{0,100}(?:not collected|not been collected|not picked up|not removed|hasn't been collected|has not been collected|uncollected)|(?:not collected|not been collected|not picked up|not removed|hasn't been collected|has not been collected|uncollected).{0,100}(?:garbage|trash|rubbish|waste)/iu,
  },
  {
    category: "Encroachment",
    reason: "The post reports illegal hawkers occupying a public place.",
    pattern: /(?:illegal|unauthori[sz]ed|unlicensed|अवैध|अनधिकृत|बेकायदेशीर).{0,60}(?:hawkers?|street vendors?|vendors?|फेरीवाले|फेरीवाला|हॉकर्स?|विक्रेते).{0,60}(?:outside|near|at|on|station|railway|metro|road|footpath|market|बाहेर|जवळ|स्थानक|स्टेशन|रस्ता|पदपथ)|(?:outside|near|at|on|station|railway|metro|road|footpath|market|बाहेर|जवळ|स्थानक|स्टेशन|रस्ता|पदपथ).{0,60}(?:illegal|unauthori[sz]ed|unlicensed|अवैध|अनधिकृत|बेकायदेशीर).{0,60}(?:hawkers?|street vendors?|vendors?|फेरीवाले|फेरीवाला|हॉकर्स?|विक्रेते)/iu,
  },
];

export function mentionsMyBmc(text = "") {
  // CivicSocial handles consist of ASCII letters, digits, and underscores.
  return /@mybmc(?![a-z0-9_])/i.test(String(text));
}

export function isCivicIssue(text = "") {
  const value = String(text);
  return CIVIC_ISSUE_PATTERNS.some(pattern => pattern.test(value));
}

export function inferClearCivicComplaint(text = "") {
  const value = String(text);
  return CLEAR_CIVIC_FALLBACKS.find(({ pattern }) => pattern.test(value)) || null;
}

export function isEligibleBmcComplaint(post) {
  const author = post?.author;
  if (!author || author.is_official || String(author.handle || "").toLowerCase() === "mybmc") return false;
  return mentionsMyBmc(post.text) && isCivicIssue(post.text);
}
