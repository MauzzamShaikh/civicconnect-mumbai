import unittest
from unittest.mock import Mock, patch

from app import geo


class LocationCandidateTests(unittest.TestCase):
    def test_generic_place_feature_does_not_count_as_landmark_identity(self):
        entity = {
            "normalized_name": "Riverside College",
            "mention": "Riverside College",
            "context": "",
            "location_type": "college",
        }
        wrong_college = {
            "lat": 19.12,
            "lon": 72.84,
            "name": "Valley College",
            "formatted": "Valley College, Mumbai, India",
            "category": "education",
            "result_type": "college",
            "rank": {"confidence": 1.0, "match_type": "full_match"},
        }

        accepted, _score, reason = geo._score_structured_geoapify(entity, wrong_college)

        self.assertFalse(accepted)
        self.assertEqual(reason, "primary_entity_tokens_missing")

    def test_railway_station_candidate_is_not_accepted_as_a_beach_in_the_same_locality(self):
        entity = {
            "normalized_name": "Khar Road Railway Station",
            "mention": "Khar Road Railway Station",
            "context": "Khar",
            "location_type": "railway station",
        }
        nearby_beach = {
            "lat": 19.06, "lon": 72.82, "name": "Carter Road Beach",
            "formatted": "Carter Road Beach, Khar, Mumbai, India",
            "category": "natural.beach", "result_type": "beach",
            "rank": {"confidence": 1.0, "match_type": "full_match"},
        }

        accepted, _score, reason = geo._score_structured_geoapify(entity, nearby_beach)

        self.assertFalse(accepted)
        self.assertEqual(reason, "requested_type_mismatch")

    def test_locality_only_groq_extraction_is_refined_to_explicit_place_type(self):
        text = "खार रोड रेल्वे स्थानकाजवळील अनधिकृत फेरीवाल्यांनी रस्त्याचा मोठा भाग व्यापला आहे"
        extraction = {
            "has_location": True,
            "is_between": False,
            "locations": [{
                "mention": "खार", "normalized_name": "Khar", "role": "primary",
                "relation": "", "context": "", "location_type": "locality", "confidence": 0.9,
            }],
        }
        verified = {"lat": 19.06, "lng": 72.84, "display_name": "Test station", "source": "geoapify"}
        with patch.object(geo, "_extract_locations_with_groq", return_value=extraction), \
             patch.object(geo, "_extract_location_candidates", return_value=["खार रोड रेल्वे स्थानक"]), \
             patch.object(geo, "_resolve_structured_entity", return_value=verified) as resolve:
            result = geo.resolve_location(text)

        self.assertEqual(result, verified)
        entity = resolve.call_args.args[0]
        self.assertEqual(entity["location_type"], "railway station")
        self.assertEqual(entity["context"], "Khar")
        self.assertIn("स्थानक", entity["normalized_name"])

    def test_devanagari_hospital_and_locality_are_normalized_for_geocoding(self):
        self.assertEqual(geo._canonical_query("सायन हॉस्पिटल"), "Sion Hospital")
        self.assertEqual(geo._location_feature_from_text("सायन हॉस्पिटलच्या प्रवेशद्वाराबाहेर"), "hospital")
        self.assertEqual(geo._known_locality_context("सायन हॉस्पिटलच्या बाहेर"), "Sion")
        candidates = geo._extract_location_candidates(
            "सायन हॉस्पिटलच्या प्रवेशद्वाराबाहेर बेकायदेशीर पार्किंगमुळे पदपथ अडवला आहे"
        )
        self.assertIn("Sion Hospital", [geo._canonical_query(candidate) for candidate in candidates])

    def test_nominatim_locality_cannot_satisfy_an_explicit_hospital_type(self):
        entity = {
            "normalized_name": "Sion Hospital", "mention": "Sion Hospital",
            "context": "Sion", "location_type": "hospital",
        }
        locality_result = {
            "name": "Sion", "display_name": "Sion, Mumbai, Maharashtra, India",
            "type": "suburb", "class": "place", "address": {"suburb": "Sion"},
        }

        self.assertFalse(geo._structured_nominatim_matches(entity, locality_result))

    def test_directional_connectors_extract_the_full_target_in_supported_scripts(self):
        fixtures = (
            ("चेंबूर के विवेकानंद कॉलेज की ओर जाने वाला फुटपाथ टूटा है", "चेंबूर के विवेकानंद कॉलेज"),
            ("थाडोमल शहाणी कॉलेजकडे जाणारा पदपथ तुटला आहे", "थाडोमल शहाणी कॉलेज"),
            ("Vivekanand College ki or jane wala footpath damaged hai", "Vivekanand College"),
        )
        for text, expected in fixtures:
            with self.subTest(text=text):
                self.assertIn(expected, geo._extract_location_candidates(text))

    def test_false_between_classification_is_recovered_as_one_directional_place(self):
        text = "थाडोमल शहाणी कॉलेजकडे जाणारा पदपथ अनेक दिवसांपासून तुटलेला आहे"
        extraction = {
            "has_location": True,
            "is_between": True,
            "locations": [
                {"normalized_name": "Thadomal", "mention": "थाडोमल", "role": "endpoint_a",
                 "context": text, "location_type": "town", "confidence": 0.9, "relation": ""},
                {"normalized_name": "Shahani", "mention": "शहाणी", "role": "endpoint_b",
                 "context": text, "location_type": "town", "confidence": 0.9, "relation": ""},
            ],
        }
        verified = {"lat": 19.06, "lng": 72.83, "source": "geoapify", "display_name": "Verified place"}
        with patch.object(geo, "_extract_locations_with_groq", return_value=extraction), \
             patch.object(geo, "_resolve_structured_entity", return_value=verified) as resolve:
            result = geo.resolve_location(text)

        self.assertEqual(result, verified)
        entity = resolve.call_args.args[0]
        self.assertEqual(entity["normalized_name"], "थाडोमल शहाणी कॉलेज")
        self.assertIn("thadomal shahani college", entity["mention"].casefold())
        self.assertEqual(entity["location_type"], "college")

    def test_unrecognized_model_locality_fragment_is_merged_into_the_landmark_name(self):
        text = "थाडोमल शहाणी कॉलेजकडे जाणारा पदपथ तुटलेला आहे"
        extraction = {
            "has_location": True,
            "is_between": False,
            "locations": [
                {"normalized_name": "Shahani College", "mention": "शहाणी कॉलेज", "role": "primary",
                 "context": "", "location_type": "college", "confidence": 0.9, "relation": ""},
                {"normalized_name": "Thadomal", "mention": "थाडोमल", "role": "locality",
                 "context": "", "location_type": "locality", "confidence": 0.9, "relation": ""},
            ],
        }
        verified = {"lat": 19.06, "lng": 72.83, "source": "geoapify", "display_name": "Verified place"}
        with patch.object(geo, "_extract_locations_with_groq", return_value=extraction), \
             patch.object(geo, "_resolve_structured_entity", return_value=verified) as resolve:
            result = geo.resolve_location(text)

        self.assertEqual(result, verified)
        entity = resolve.call_args.args[0]
        self.assertEqual(entity["normalized_name"], "थाडोमल शहाणी कॉलेज")
        self.assertIn("thadomal", entity["mention"].casefold())
        self.assertIn("shahani", entity["mention"].casefold())

    def test_feature_phrase_fallback_extracts_a_landmark_without_connector(self):
        candidates = geo._extract_location_candidates(
            "Don Bosco Institute of Technology has a large pothole"
        )

        self.assertIn("Don Bosco Institute of Technology", candidates)

    def test_marathi_inflected_near_connector_keeps_landmark_and_locality(self):
        candidates = geo._extract_location_candidates(
            "मालाड पश्चिम येथील मित्तल कॉलेज जवळील रस्त्याची दुरावस्था झाली असून "
            "संपूर्ण रस्ता चिखलमय झाला आहे आणि रस्त्यावर प्रचंड मोठे खड्डे पडले आहेत."
        )

        self.assertIn("मित्तल कॉलेज, मालाड पश्चिम", candidates)
        self.assertFalse(any("रस्त्याची दुरावस्था" in candidate for candidate in candidates))

    def test_locality_extraction_supports_marathi_hindi_and_romanized_text(self):
        samples = (
            ("पवई येथे मोठा खड्डा आहे", "पवई"),
            ("घाटकोपर में कचरा जमा है", "घाटकोपर"),
            ("ghatkopar mein kachra jama hai", "ghatkopar"),
        )
        for text, expected in samples:
            with self.subTest(text=text):
                self.assertIn(expected, geo._extract_location_candidates(text))

    def test_footpath_encroachment_adds_shared_cluster_tags_across_supported_languages(self):
        fixtures = (
            "@mybmc The footpath outside the college has been encroached",
            "कॉलेजबाहेरील पदपथावर अतिक्रमणे झाली आहेत",
            "college ke bahar footpath par atikraman hua hai",
            "कॉलेज के बाहर फुटपाथ पर अतिक्रमण हुआ है",
        )

        for text in fixtures:
            with self.subTest(text=text):
                normalized = geo.normalize_cluster_text(text, include_issue_tags=True)
                self.assertIn("Canonical issue:", normalized)
                self.assertIn("footpath", normalized.split("Canonical issue:", 1)[1])
                self.assertIn("encroachment", normalized.split("Canonical issue:", 1)[1])

    def test_multilingual_illegal_parking_blocking_access_has_shared_clustering_tags(self):
        examples = (
            "Illegal parking is blocking the entrance and footpath",
            "सायन हॉस्पिटलच्या प्रवेशद्वाराबाहेर बेकायदेशीर पार्किंगमुळे पदपथ अडवले गेले आहेत",
            "अस्पताल के बाहर अवैध पार्किंग से फुटपाथ और प्रवेश द्वार बंद हैं",
        )
        for text in examples:
            with self.subTest(text=text):
                normalized = geo.normalize_cluster_text(text, include_issue_tags=True)
                tags = normalized.split("Canonical issue:", 1)[-1]
                self.assertIn("footpath", tags)
                self.assertIn("encroachment", tags)

    def test_geoapify_result_from_conflicting_locality_is_rejected(self):
        entity = {
            "normalized_name": "DJ Sanghvi College",
            "context": "Bandra West",
            "location_type": "college",
        }
        result = {
            "lat": 19.06,
            "lon": 72.84,
            "name": "DJ Sanghvi College",
            "formatted": "DJ Sanghvi College, Bandra East, Mumbai",
            "category": "education",
            "result_type": "college",
        }

        accepted, _score, reason = geo._score_structured_geoapify(entity, result)

        self.assertFalse(accepted)
        self.assertEqual(reason, "context_tokens_missing")

    def test_geoapify_structured_address_fields_can_confirm_locality_context(self):
        entity = {
            "normalized_name": "Fatima High School",
            "context": "Vidyavihar West",
            "location_type": "school",
        }
        result = {
            "lat": 19.05,
            "lon": 72.90,
            "name": "Fatima High School",
            "formatted": "Fatima High School, Mumbai, India",
            "address_line2": "Vidyavihar West, Mumbai",
            "category": "education",
            "result_type": "school",
        }

        accepted, _score, reason = geo._score_structured_geoapify(entity, result)

        self.assertTrue(accepted, reason)

    def test_exact_landmark_without_locality_metadata_uses_nearby_locality_confirmation(self):
        entity = {
            "normalized_name": "Shah and Anchor College",
            "mention": "Shah and Anchor College",
            "context": "Chembur",
            "location_type": "college",
        }
        landmark = {
            "lat": 19.045,
            "lon": 72.900,
            "name": "Shah & Anchor Kutchhi Engineering College",
            "formatted": "Shah & Anchor Kutchhi Engineering College, Mumbai, India",
            "category": "education",
            "result_type": "college",
        }
        locality = {
            "lat": 19.052,
            "lon": 72.900,
            "name": "Chembur",
            "formatted": "Chembur, Mumbai, Maharashtra, India",
            "category": "administrative",
            "result_type": "suburb",
        }

        def response_for_query(_url, *, params, timeout):
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"results": [locality if "College" not in params["text"] else landmark]}
            return response

        with patch.dict("os.environ", {"GEOAPIFY_API_KEY": "test-key"}), \
             patch.object(geo, "_geoapify_cache", {}), \
             patch.object(geo._geoapify_session, "get", side_effect=response_for_query):
            resolved = geo._geoapify_structured_search(entity, timeout=1)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["source"], "geoapify")
        self.assertEqual((resolved["lat"], resolved["lng"]), (landmark["lat"], landmark["lon"]))

    def test_school_without_locality_metadata_is_confirmed_near_matching_locality(self):
        entity = {
            "normalized_name": "Fatima High School",
            "mention": "Fatima High School",
            "context": "Vidyavihar West",
            "location_type": "school",
        }
        school = {
            "lat": 19.075,
            "lon": 72.896,
            "name": "Fatima High School",
            "formatted": "Fatima High School, Ghatkopar West, Mumbai, India",
            "category": "education",
            "result_type": "school",
        }
        locality = {
            "lat": 19.078,
            "lon": 72.897,
            "name": "Vidyavihar West",
            "formatted": "Vidyavihar West, Mumbai, Maharashtra, India",
            "category": "administrative",
            "result_type": "suburb",
        }

        def response_for_query(_url, *, params, timeout):
            response = Mock()
            response.raise_for_status.return_value = None
            query = params["text"]
            if "Fatima High School" in query:
                results = [school]
            elif "Vidyavihar West" in query:
                results = [{
                    "lat": 19.06, "lon": 72.84, "name": "Bandra West",
                    "formatted": "Bandra West, Mumbai", "category": "place", "result_type": "locality",
                }]
            else:
                results = [locality]
            response.json.return_value = {"results": results}
            return response

        nominatim_empty = Mock()
        nominatim_empty.raise_for_status.return_value = None
        nominatim_empty.json.return_value = []

        with patch.dict("os.environ", {"GEOAPIFY_API_KEY": "test-key"}), \
             patch.object(geo, "_geoapify_cache", {}), \
             patch.object(geo, "_nominatim_cache", {}), \
             patch.object(geo._geoapify_session, "get", side_effect=response_for_query), \
             patch.object(geo.requests, "get", return_value=nominatim_empty):
            resolved = geo._geoapify_structured_search(entity, timeout=1)

        self.assertIsNotNone(resolved)
        self.assertEqual((resolved["lat"], resolved["lng"]), (school["lat"], school["lon"]))
        self.assertIn("Ghatkopar West", resolved["display_name"])
        self.assertIn("near Vidyavihar West", resolved["display_name"])
        self.assertIn("Vidyavihar West", resolved["canonical_name"])

    def test_landmark_outside_requested_locality_radius_stays_unresolved(self):
        entity = {
            "normalized_name": "Fatima High School",
            "mention": "Fatima High School",
            "context": "Vidyavihar West",
            "location_type": "school",
        }
        distant_landmark = {
            "lat": 19.060,
            "lon": 72.840,
            "name": "Fatima High School",
            "formatted": "Fatima High School, Mumbai, India",
            "category": "education",
            "result_type": "school",
        }
        locality = {
            "lat": 19.080,
            "lon": 72.900,
            "name": "Vidyavihar West",
            "formatted": "Vidyavihar West, Mumbai, India",
            "category": "administrative",
            "result_type": "suburb",
        }

        def response_for_query(_url, *, params, timeout):
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"results": [locality if "Fatima High School" not in params["text"] else distant_landmark]}
            return response

        with patch.dict("os.environ", {"GEOAPIFY_API_KEY": "test-key"}), \
             patch.object(geo, "_geoapify_cache", {}), \
             patch.object(geo._geoapify_session, "get", side_effect=response_for_query):
            resolved = geo._geoapify_structured_search(entity, timeout=1)

        self.assertIsNone(resolved)

    def test_nominatim_locality_can_corroborate_landmark_when_geoapify_lacks_locality(self):
        entity = {
            "normalized_name": "Fatima High School",
            "mention": "Fatima High School",
            "context": "Vidyavihar West",
            "location_type": "school",
        }
        school = {
            "lat": 19.075,
            "lon": 72.896,
            "name": "Fatima High School",
            "formatted": "Fatima High School, Mumbai, India",
            "category": "education",
            "result_type": "school",
        }
        def geoapify_get(_url, *, params, timeout):
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"results": [school] if "Fatima High School" in params["text"] else []}
            return response

        nominatim_response = Mock()
        nominatim_response.raise_for_status.return_value = None
        nominatim_response.json.return_value = [{
            "lat": "19.078",
            "lon": "72.897",
            "display_name": "Vidyavihar West, Mumbai, Maharashtra, India",
            "class": "place",
            "type": "suburb",
            "address": {"suburb": "Vidyavihar West", "city": "Mumbai"},
        }]

        with patch.dict("os.environ", {"GEOAPIFY_API_KEY": "test-key"}), \
             patch.object(geo, "_geoapify_cache", {}), \
             patch.object(geo, "_nominatim_cache", {}), \
             patch.object(geo._geoapify_session, "get", side_effect=geoapify_get), \
             patch.object(geo.requests, "get", return_value=nominatim_response):
            resolved = geo._geoapify_structured_search(entity, timeout=1)

        self.assertIsNotNone(resolved)
        self.assertEqual((resolved["lat"], resolved["lng"]), (school["lat"], school["lon"]))
        self.assertEqual(resolved["source"], "geoapify")

    def test_geoapify_accepts_original_place_mention_when_normalized_name_is_incomplete(self):
        entity = {
            "normalized_name": "Don Bosco Institute",
            "mention": "Don Bosco Institute of Technology",
            "context": "Kurla West",
            "location_type": "college",
        }
        result = {
            "lat": 19.07,
            "lon": 72.88,
            "name": "Don Bosco Institute of Technology",
            "formatted": "Don Bosco Institute of Technology, Kurla West, Mumbai",
            "category": "education",
            "result_type": "college",
        }

        accepted, _score, reason = geo._score_structured_geoapify(entity, result)

        self.assertTrue(accepted, reason)

    def test_structured_query_variants_keep_locality_for_original_mention(self):
        entity = {
            "normalized_name": "Don Bosco Institute",
            "mention": "Don Bosco Institute of Technology",
            "context": "Kurla West",
        }

        queries = geo._structured_query_variants(entity)

        self.assertEqual(len(queries), 4)
        self.assertTrue(all("Kurla West" in query for query in queries[:2]))
        self.assertIn("Don Bosco Institute of Technology", queries[1])
        self.assertTrue(all("Kurla West" not in query for query in queries[2:]))

    def test_broad_geoapify_query_can_resolve_only_with_matching_locality_evidence(self):
        entity = {
            "normalized_name": "Fatima High School",
            "mention": "Fatima High School",
            "context": "Vidyavihar West",
            "location_type": "school",
        }
        irrelevant = Mock()
        irrelevant.json.return_value = {"results": [{
            "lat": 19.06,
            "lon": 72.84,
            "name": "Bandra West",
            "formatted": "Bandra West, Mumbai",
            "category": "place",
            "result_type": "locality",
        }]}
        matching = Mock()
        matching.json.return_value = {"results": [{
            "lat": 19.02,
            "lon": 72.84,
            "name": "Fatima High School",
            "formatted": "Fatima High School, Vidyavihar West, Mumbai",
            "category": "education",
            "result_type": "school",
        }]}
        with patch.dict("os.environ", {"GEOAPIFY_API_KEY": "test-key"}), \
             patch.object(geo, "_geoapify_cache", {}), \
             patch.object(geo._geoapify_session, "get", side_effect=(irrelevant, matching)) as request:
            resolved = geo._geoapify_structured_search(entity, timeout=1)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["source"], "geoapify")
        self.assertIn("Vidyavihar West", resolved["display_name"])
        self.assertEqual(request.call_count, 2)
        self.assertIn("Vidyavihar West", request.call_args_list[0].kwargs["params"]["text"])
        self.assertNotIn("Vidyavihar West", request.call_args_list[1].kwargs["params"]["text"])

    def test_broad_geoapify_query_still_rejects_conflicting_locality(self):
        entity = {
            "normalized_name": "Fatima High School",
            "mention": "Fatima High School",
            "context": "Vidyavihar West",
            "location_type": "school",
        }
        wrong_locality = Mock()
        wrong_locality.json.return_value = {"results": [{
            "lat": 19.06,
            "lon": 72.84,
            "name": "Fatima High School",
            "formatted": "Fatima High School, Bandra West, Mumbai",
            "category": "education",
            "result_type": "school",
        }]}
        with patch.dict("os.environ", {"GEOAPIFY_API_KEY": "test-key"}), \
             patch.object(geo, "_geoapify_cache", {}), \
             patch.object(geo._geoapify_session, "get", return_value=wrong_locality):
            resolved = geo._geoapify_structured_search(entity, timeout=1)

        self.assertIsNone(resolved)

    def test_long_model_context_uses_separate_known_locality_entity(self):
        primary = {
            "normalized_name": "Mittal College",
            "context": "मालाड पश्चिम येथील मित्तल कॉलेज जवळील रस्त्याची दुरावस्था झाली",
        }
        context_entity = {"normalized_name": "Malad West", "role": "context"}

        resolved = geo._retain_complaint_locality(
            primary,
            "मालाड पश्चिम येथील मित्तल कॉलेज जवळील रस्त्याची दुरावस्था झाली",
            [context_entity],
        )

        self.assertEqual(resolved["context"], "Malad West")

    def test_context_that_is_complaint_prose_is_not_sent_as_locality(self):
        primary = {
            "normalized_name": "JJ Signal",
            "context": "Traffic light is not working at jj signal",
        }

        resolved = geo._retain_complaint_locality(
            primary,
            "Traffic light is not working at jj signal",
        )

        self.assertEqual(resolved["context"], "")

    def test_locality_is_recovered_from_prose_context_with_known_locality(self):
        primary = {
            "normalized_name": "Fatima High School",
            "context": "Illegal food stalls outside Fatima High School in Vidyavihar West causing food poisoning",
        }

        resolved = geo._retain_complaint_locality(
            primary,
            primary["context"],
        )

        self.assertEqual(resolved["context"], "Vidyavihar West")

    def test_resolver_uses_context_role_entity_to_correct_primary_query(self):
        extraction = {
            "has_location": True,
            "is_between": False,
            "locations": [
                {
                    "mention": "Mittal College",
                    "normalized_name": "Mittal College",
                    "role": "primary",
                    "relation": "near",
                    "context": "मालाड पश्चिम येथील मित्तल कॉलेज जवळील रस्त्याची दुरावस्था झाली",
                    "location_type": "college",
                    "confidence": 0.9,
                },
                {
                    "mention": "Malad West",
                    "normalized_name": "Malad West",
                    "role": "context",
                    "relation": "in",
                    "context": "",
                    "location_type": "locality",
                    "confidence": 0.9,
                },
            ],
        }
        with patch.object(geo, "_extract_locations_with_groq", return_value=extraction), \
             patch.object(geo, "_resolve_structured_entity", return_value=None) as resolve, \
             patch.object(geo, "_extract_location_candidates", return_value=[]), \
             self.assertLogs(geo.logger, level="INFO") as captured:
            geo.resolve_location("मालाड पश्चिम येथील मित्तल कॉलेज जवळ खड्डे")

        self.assertEqual(resolve.call_args.args[0]["normalized_name"], "Mittal College")
        self.assertEqual(resolve.call_args.args[0]["context"], "Malad West")
        self.assertNotIn("मालाड पश्चिम येथील मित्तल कॉलेज जवळ खड्डे", "\n".join(captured.output))

    def test_entity_diagnostics_do_not_log_raw_complaint_prose_from_context(self):
        complaint_text = "Illegal food stalls outside Fatima High School in Vidyavihar West causing food poisoning"
        extraction = {
            "has_location": True,
            "is_between": False,
            "locations": [{
                "mention": "Fatima High School",
                "normalized_name": "Fatima High School",
                "role": "primary",
                "relation": "outside",
                "context": complaint_text,
                "location_type": "school",
                "confidence": 0.9,
            }],
        }
        with patch.object(geo, "_extract_locations_with_groq", return_value=extraction), \
             patch.object(geo, "_resolve_structured_entity", return_value=None), \
             patch.object(geo, "_extract_location_candidates", return_value=[]), \
             self.assertLogs(geo.logger, level="INFO") as captured:
            geo.resolve_location(complaint_text)

        logs = "\n".join(captured.output)
        self.assertIn("context_has_known_locality=True", logs)
        self.assertIn("context='Vidyavihar West'", logs)
        self.assertNotIn(complaint_text, logs)
        self.assertNotIn("food poisoning", logs)

    def test_nominatim_accepts_original_place_mention_but_still_checks_context(self):
        entity = {
            "normalized_name": "Don Bosco Institute",
            "mention": "Don Bosco Institute of Technology",
            "context": "Kurla West",
        }
        correct = {
            "name": "Don Bosco Institute of Technology",
            "display_name": "Don Bosco Institute of Technology, Kurla West, Mumbai",
        }
        conflicting = {
            "name": "Don Bosco Institute of Technology",
            "display_name": "Don Bosco Institute of Technology, Bandra West, Mumbai",
        }

        self.assertTrue(geo._structured_nominatim_matches(entity, correct))
        self.assertFalse(geo._structured_nominatim_matches(entity, conflicting))

    def test_rejection_diagnostics_show_matching_fields_without_text_secrets_or_coordinates(self):
        complaint_text = "PRIVATE_COMPLAINT_TEXT: pothole at a confidential address"
        entity = {
            "normalized_name": "Don Bosco Institute of Technology",
            "mention": "Don Bosco Institute of Technology",
            "context": "Kurla West",
            "location_type": "college",
        }
        response = Mock()
        response.json.return_value = {"results": [{
            "lat": 19.06,
            "lon": 72.84,
            "name": "Don Bosco Institute of Technology",
            "formatted": "Don Bosco Institute of Technology, Bandra West, Mumbai",
            "category": "education",
            "result_type": "college",
        }]}
        with patch.dict("os.environ", {"GEOAPIFY_API_KEY": "PRIVATE_GEOAPIFY_SECRET"}), \
             patch.object(geo._geoapify_session, "get", return_value=response), \
             self.assertLogs(geo.logger, level="INFO") as captured:
            result = geo._geoapify_structured_search(entity, timeout=1)

        self.assertIsNone(result)
        logs = "\n".join(captured.output)
        self.assertIn("LOCATION_CANDIDATE provider=geoapify outcome=rejected", logs)
        self.assertIn("requested_name='Don Bosco Institute of Technology'", logs)
        self.assertIn("requested_context='Kurla West'", logs)
        self.assertIn("candidate_name='Don Bosco Institute of Technology'", logs)
        self.assertIn("missing_context_tokens=kurla", logs)
        self.assertNotIn(complaint_text, logs)
        self.assertNotIn("PRIVATE_GEOAPIFY_SECRET", logs)
        self.assertNotIn("19.06", logs)
        self.assertNotIn("72.84", logs)

    def test_nominatim_context_uses_returned_address_not_query_text(self):
        entity = {"normalized_name": "DJ Sanghvi College", "context": "Bandra West"}
        result = {
            "name": "DJ Sanghvi College",
            "display_name": "DJ Sanghvi College, Bandra East, Mumbai",
            # This is query metadata; it must not count as returned location evidence.
            "canonical_name": "DJ Sanghvi College, Bandra West, Mumbai",
        }

        self.assertFalse(geo._structured_nominatim_matches(entity, result))

    def test_contextual_provider_failure_returns_unresolved_without_unsafe_fallback(self):
        extraction = {
            "has_location": True,
            "is_between": False,
            "locations": [{
                "mention": "DJ Sanghvi College in Bandra West",
                "normalized_name": "DJ Sanghvi College",
                "role": "primary",
                "relation": "in",
                "context": "Bandra West",
                "location_type": "college",
                "confidence": 0.9,
            }],
        }
        with patch.object(geo, "_extract_locations_with_groq", return_value=extraction), \
             patch.object(geo, "_resolve_structured_entity", return_value=None), \
             patch.object(geo, "_extract_location_candidates") as legacy_extractor:
            result = geo.resolve_location("DJ Sanghvi College in Bandra West")

        self.assertIsNone(result["lat"])
        self.assertIsNone(result["lng"])
        self.assertEqual(result["source"], "ward_fallback")
        legacy_extractor.assert_not_called()

    def test_provider_failure_without_verified_locality_returns_null_coordinates(self):
        with patch.object(geo, "_extract_locations_with_groq", return_value=None), \
             patch.object(geo, "_resolve_candidate_variants", return_value=None):
            result = geo.resolve_location("Potholes near an unnamed place")

        self.assertIsNone(result["lat"])
        self.assertIsNone(result["lng"])
        self.assertEqual(result["source"], "none")

    def test_location_diagnostics_are_info_level_and_exclude_complaint_text(self):
        complaint_text = "PRIVATE-LOCATION-DIAGNOSTIC pothole near an unnamed place"
        with self.assertLogs(geo.logger, level="INFO") as captured, \
             patch.object(geo, "Groq", None), \
             patch.dict("os.environ", {"GROQ_API_KEY": ""}), \
             patch.object(geo, "_extract_location_candidates", return_value=[]):
            result = geo.resolve_location(complaint_text)

        self.assertEqual(result["source"], "none")
        logs = "\n".join(captured.output)
        self.assertIn("LOCATION_STAGE provider=groq outcome=skipped", logs)
        self.assertIn("LOCATION_RESULT source=none coordinates=unresolved", logs)
        self.assertNotIn(complaint_text, logs)

    def test_between_place_geocoding_is_explicitly_marked_as_approximate(self):
        extraction = {
            "has_location": True,
            "is_between": True,
            "locations": [
                {"normalized_name": "Library", "role": "endpoint_a", "context": "Powai"},
                {"normalized_name": "Market", "role": "endpoint_b", "context": "Powai"},
            ],
        }
        endpoint_results = iter((
            {"lat": 19.12, "lng": 72.90, "source": "geoapify"},
            {"lat": 19.121, "lng": 72.901, "source": "nominatim"},
        ))
        with patch.object(geo, "_extract_locations_with_groq", return_value=extraction), \
             patch.object(geo, "_resolve_structured_entity", side_effect=lambda *_args: next(endpoint_results)):
            result = geo.resolve_location("between the library and market in Powai")

        self.assertEqual(result["source"], "geoapify_midpoint")
        self.assertIn("Approximate midpoint", result["display_name"])
        self.assertAlmostEqual(result["lat"], 19.1205)

    def test_multilingual_named_place_fixtures_keep_landmark_and_locality_context(self):
        fixtures = (
            ("Thadomal Shahani College येथे अन्नातून विषबाधा", "Thadomal Shahani College", "Bandra West"),
            ("DJ Sanghvi College in Vile Parle pothole", "DJ Sanghvi College", "Vile Parle"),
            ("शहा अँकर कॉलेज चेंबूर बाहेर खड्डा", "Shah and Anchor College", "Chembur"),
        )
        for text, landmark, locality in fixtures:
            extraction = {
                "has_location": True,
                "is_between": False,
                "locations": [{
                    "mention": f"{landmark}, {locality}",
                    "normalized_name": landmark,
                    "role": "primary",
                    "relation": "near",
                    "context": locality,
                    "location_type": "college",
                    "confidence": 0.9,
                }],
            }
            with self.subTest(landmark=landmark), \
                 patch.object(geo, "_extract_locations_with_groq", return_value=extraction), \
                 patch.object(geo, "_resolve_structured_entity", return_value={
                     "lat": 19.1, "lng": 72.9, "display_name": f"{landmark}, {locality}",
                     "source": "nominatim",
                 }) as resolve:
                result = geo.resolve_location(text)

            self.assertEqual(result["source"], "nominatim")
            entity = resolve.call_args.args[0]
            self.assertEqual(entity["normalized_name"], landmark)
            self.assertEqual(entity["context"], locality)


if __name__ == "__main__":
    unittest.main()
