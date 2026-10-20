"""Citizen complaint API and clustering tests with a stubbed inference engine."""

import base64
import asyncio
import json
import math
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth, geo, main, services
from app.db import Base
from app.models import Complaint, ComplaintStatusHistory, ComplaintSubscription, Notification, User


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


class FakeInference:
    def __init__(self, photo_result="High", video_result="Low"):
        self.scored_images = []
        self.processed_videos = []
        self.photo_result = photo_result
        self.video_result = video_result

    def embedding(self, _text):
        return np.asarray([1.0, 0.0], dtype=float)

    def text_severity(self, _embedding):
        return "Medium"

    def photo_severity(self, image):
        self.scored_images.append(image)
        return self.photo_result

    def process_video(self, original_video):
        self.processed_videos.append(original_video)
        return base64.b64encode(b"representative-frame").decode(), self.video_result, "transcribed words", True


class CitizenComplaintApiTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(prefix="citizen-complaints-", suffix=".sqlite")
        os.close(handle)
        self.engine = create_engine(
            f"sqlite:///{self.database_path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.citizen = self._user("citizen@example.com", "citizen")
        self.other = self._user("other@example.com", "citizen")
        self.staff = self._user("staff@example.com", "admin")
        self._old_secret = os.environ.get("JWT_SECRET_KEY")
        self._old_civicsocial_secret = os.environ.get("CIVICSOCIAL_INTEGRATION_SECRET")
        self._old_session_local = main.SessionLocal
        os.environ["JWT_SECRET_KEY"] = "test-only-secret-key-that-is-at-least-32-bytes"
        main.SessionLocal = self.Session
        auth._LOGIN_FAILURES.clear()
        auth._RATE_EVENTS.clear()

        def override_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        main.app.dependency_overrides[main.get_db] = override_db
        main.app.dependency_overrides[auth.get_db] = override_db
        self.client = TestClient(main.app)
        self.citizen_headers = self._headers(self.citizen)
        self.other_headers = self._headers(self.other)
        self.staff_headers = self._headers(self.staff)

    def tearDown(self):
        main.app.dependency_overrides.clear()
        main.SessionLocal = self._old_session_local
        self.db.close()
        self.engine.dispose()
        if self._old_secret is None:
            os.environ.pop("JWT_SECRET_KEY", None)
        else:
            os.environ["JWT_SECRET_KEY"] = self._old_secret
        if self._old_civicsocial_secret is None:
            os.environ.pop("CIVICSOCIAL_INTEGRATION_SECRET", None)
        else:
            os.environ["CIVICSOCIAL_INTEGRATION_SECRET"] = self._old_civicsocial_secret
        try:
            os.unlink(self.database_path)
        except FileNotFoundError:
            pass

    def _user(self, email, role):
        user = User(
            name=email.split("@")[0], email=email, password_hash="test-hash",
            role=role, email_verified=True, is_active=True,
        )
        self.db.add(user)
        self.db.commit()
        return user

    @staticmethod
    def _headers(user):
        return {"Authorization": f"Bearer {auth._create_access_token(user)}"}

    def _saved_complaint(self, *, citizen_id, complaint_id, embedding=None, lat=19.076, lng=72.878, location_source=None):
        complaint = Complaint(
            id=complaint_id,
            citizen_id=citizen_id,
            text="Pothole near the road",
            language="en",
            location="Ward/locality from text",
            location_source=location_source or ("nominatim" if lat is not None and lng is not None else "none"),
            cluster_id=int(complaint_id[-1]) if complaint_id[-1].isdigit() else 1,
            embedding=embedding or [1.0, 0.0],
            text_severity="Medium",
            final_severity="Medium",
            final_score=2.0,
            has_media=False,
            has_video=False,
            is_real=True,
            status="open",
            lat=lat,
            lng=lng,
        )
        self.db.add(complaint)
        self.db.commit()
        return complaint

    def test_anonymous_submission_is_rejected(self):
        response = self.client.post("/citizen/complaints", json={"text": "A pothole by my street"})
        self.assertEqual(response.status_code, 401)

    def test_staff_submission_stores_canonical_category_without_changing_severity(self):
        with patch.object(services, "get_infer", return_value=FakeInference()), patch.object(
            services, "resolve_location", return_value={"lat": None, "lng": None, "display_name": None, "source": "none"}
        ):
            response = self.client.post(
                "/complaints", headers=self.staff_headers,
                json={"text": "A large pothole has formed on the road"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["category"], "Roads")
        self.assertEqual(response.json()["severity"], "Medium")
        self.assertEqual(self.db.get(Complaint, response.json()["id"]).category, "Roads")

    def test_disallowed_role_is_rejected(self):
        response = self.client.post("/citizen/complaints", headers=self.staff_headers, json={"text": "A pothole by my street"})
        self.assertEqual(response.status_code, 403)

    def test_bmc_data_and_action_routes_require_staff_role(self):
        complaint = self._saved_complaint(citizen_id=None, complaint_id="CMP-GATE001")
        read_paths = (
            "/complaints",
            "/clusters",
            "/map-approximations",
            f"/clusters/{complaint.cluster_id}",
            f"/bmc/complaints/{complaint.id}/media",
        )
        for path in read_paths:
            self.assertEqual(self.client.get(path).status_code, 401, path)
            self.assertEqual(self.client.get(path, headers=self.citizen_headers).status_code, 403, path)

        post_requests = (
            ("/complaints", {"text": "A pothole by my street"}),
        )
        for path, payload in post_requests:
            self.assertEqual(self.client.post(path, json=payload).status_code, 401, path)
            self.assertEqual(self.client.post(path, headers=self.citizen_headers, json=payload).status_code, 403, path)

        status_path = f"/complaints/{complaint.id}/status"
        self.assertEqual(self.client.patch(status_path, json={"status": "resolved"}).status_code, 401)
        self.assertEqual(self.client.patch(status_path, headers=self.citizen_headers, json={"status": "resolved"}).status_code, 403)

        self.assertEqual(self.client.get("/complaints", headers=self.staff_headers).status_code, 200)
        self.assertEqual(self.client.get("/clusters", headers=self.staff_headers).status_code, 200)
        self.assertEqual(
            self.client.get(f"/clusters/{complaint.cluster_id}", headers=self.staff_headers).status_code,
            200,
        )

    def test_client_cannot_supply_ownership_id(self):
        response = self.client.post(
            "/citizen/complaints",
            headers=self.citizen_headers,
            json={"text": "A pothole by my street", "citizen_id": self.other.id},
        )
        self.assertEqual(response.status_code, 422)

    def test_stubbed_inference_submission_persists_owner_and_uses_pin(self):
        body = {"text": "A pothole by my street", "lat": 19.076, "lng": 72.878}
        with patch.object(services, "get_infer", return_value=FakeInference()), patch.object(
            services, "resolve_location", side_effect=AssertionError("pin must bypass text geocoding")
        ), patch.object(services, "assign_citizen_pinned_cluster", wraps=services.assign_citizen_pinned_cluster) as pin_cluster, patch.object(
            main, "process_submission", wraps=services.process_submission
        ) as process:
            response = self.client.post("/citizen/complaints", headers=self.citizen_headers, json=body)
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertTrue(result["id"].startswith("CMP-"))
        self.assertEqual(result["status"], "pending_review")
        saved = self.db.get(Complaint, result["id"])
        self.assertIsNotNone(saved)
        self.assertEqual(saved.citizen_id, self.citizen.id)
        self.assertEqual(saved.status, "open")
        self.assertEqual(saved.review_state, "pending")
        self.assertEqual(saved.cluster_id, -1)
        self.assertEqual((saved.lat, saved.lng), (19.076, 72.878))
        self.assertEqual(saved.location, "Selected map point (19.07600, 72.87800)")
        self.assertEqual(saved.location_source, "citizen_pin")
        self.assertIsNone(saved.ward_code)
        pin_cluster.assert_not_called()
        self.assertEqual(process.call_args.kwargs["citizen_id"], self.citizen.id)
        self.assertEqual(process.call_args.kwargs["pinned_location"], (19.076, 72.878))

    def test_accepting_pinned_submission_assigns_it_to_the_existing_active_cluster(self):
        anchor = self._saved_complaint(citizen_id=None, complaint_id="CMP-PINNED001")
        anchor.cluster_id = 1
        anchor.review_state = "accepted"
        anchor.embedding = [1.0, 0.0]
        anchor.lat, anchor.lng = 19.076, 72.878
        self.db.commit()
        with patch.object(services, "get_infer", return_value=FakeInference()), patch.object(
            services, "resolve_location", side_effect=AssertionError("pin must bypass text geocoding")
        ), patch.object(services, "assign_citizen_pinned_cluster", wraps=services.assign_citizen_pinned_cluster) as pin_cluster:
            submitted = self.client.post("/citizen/complaints", headers=self.citizen_headers, json={
                "text": "A pothole by my street", "lat": 19.076, "lng": 72.878,
            })
            self.assertEqual(submitted.status_code, 201, submitted.text)
            complaint_id = submitted.json()["id"]
            pin_cluster.assert_not_called()
            accepted = self.client.post(f"/bmc/reviews/{complaint_id}/accept", headers=self.staff_headers)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            pin_cluster.assert_called_once()
            self.assertEqual(accepted.json()["cluster_id"], 1)
            self.assertIn(complaint_id, {row["id"] for row in self.client.get("/clusters/1", headers=self.staff_headers).json()})

    def test_staff_transition_policy_matches_all_work_status_pairs(self):
        statuses = ("open", "inspection_scheduled", "contractor_allotted", "in_progress", "resolved")
        case_number = 0
        for source in statuses:
            for target in statuses:
                if source == target:
                    continue
                complaint = self._saved_complaint(citizen_id=None, complaint_id=f"CMP-TRANS{case_number:03d}")
                case_number += 1
                complaint.status = source
                complaint.review_state = "accepted"
                self.db.commit()
                response = self.client.patch(
                    f"/complaints/{complaint.id}/status", headers=self.staff_headers,
                    json={"status": target},
                )
                expected = 200 if target in main.STAFF_TRANSITIONS[source] else 409
                self.assertEqual(response.status_code, expected, f"{source} -> {target}: {response.text}")

    def test_citizen_pinned_cluster_within_100_metres_and_semantic_threshold_matches(self):
        self._saved_complaint(citizen_id=None, complaint_id="CMP-CLUSTER1")
        delta_lat = 99 / 111_195
        cluster_id = services.assign_citizen_pinned_cluster(self.db, [1.0, 0.0], 19.076 + delta_lat, 72.878)
        self.assertEqual(cluster_id, 1)

    def test_citizen_pinned_cluster_beyond_100_metres_creates_new_cluster(self):
        self._saved_complaint(citizen_id=None, complaint_id="CMP-CLUSTER1")
        delta_lat = 101 / 111_195
        cluster_id = services.assign_citizen_pinned_cluster(self.db, [1.0, 0.0], 19.076 + delta_lat, 72.878)
        self.assertEqual(cluster_id, 2)

    def test_citizen_pinned_cluster_below_semantic_threshold_creates_new_cluster(self):
        self._saved_complaint(citizen_id=None, complaint_id="CMP-CLUSTER1")
        cluster_id = services.assign_citizen_pinned_cluster(self.db, [0.0, 1.0], 19.076, 72.878)
        self.assertEqual(cluster_id, 2)

    def test_citizen_pinned_cluster_without_existing_coordinates_does_not_match(self):
        self._saved_complaint(citizen_id=None, complaint_id="CMP-CLUSTER1", lat=None, lng=None)
        cluster_id = services.assign_citizen_pinned_cluster(self.db, [1.0, 0.0], 19.076, 72.878)
        self.assertEqual(cluster_id, 2)

    def test_legacy_bmc_clustering_keeps_existing_three_km_behavior(self):
        self._saved_complaint(citizen_id=None, complaint_id="CMP-CLUSTER1")
        delta_lat = 2_000 / 111_195
        cluster_id = services.assign_cluster(self.db, [1.0, 0.0], 19.076 + delta_lat, 72.878)
        self.assertEqual(cluster_id, 1)

    def test_distant_reports_do_not_merge_even_with_identical_embeddings(self):
        self._saved_complaint(citizen_id=None, complaint_id="CMP-CLUSTER1")
        delta_lat = 4_000 / 111_195

        cluster_id = services.assign_cluster(self.db, [1.0, 0.0], 19.076 + delta_lat, 72.878)

        self.assertEqual(cluster_id, 2)

    def test_semantically_distinct_reports_do_not_merge_at_the_same_point(self):
        self._saved_complaint(citizen_id=None, complaint_id="CMP-CLUSTER1", embedding=[0.0, 1.0])

        cluster_id = services.assign_cluster(self.db, [1.0, 0.0], 19.076, 72.878)
        citizen_pin_cluster_id = services.assign_citizen_pinned_cluster(self.db, [1.0, 0.0], 19.076, 72.878)

        self.assertEqual(cluster_id, 2)
        self.assertEqual(citizen_pin_cluster_id, 2)

    def test_related_reports_can_merge_near_the_same_specific_location(self):
        self._saved_complaint(citizen_id=None, complaint_id="CMP-CLUSTER1", embedding=[1.0, 0.0])
        delta_lat = 25 / 111_195

        cluster_id = services.assign_cluster(self.db, [0.6, 0.8], 19.076 + delta_lat, 72.878)

        self.assertEqual(cluster_id, 1)

    def test_cross_language_normalized_issue_similarity_merges_at_same_verified_location(self):
        self._saved_complaint(citizen_id=None, complaint_id="CMP-CROSSLANG1", embedding=[1.0, 0.0])
        similarity = 0.7665
        orthogonal = math.sqrt(1 - similarity ** 2)

        cluster_id = services.assign_cluster(
            self.db,
            [similarity, orthogonal],
            19.076,
            72.878,
        )

        self.assertEqual(cluster_id, 1)

    def test_ward_approximate_coordinates_do_not_join_or_create_map_clusters(self):
        self._saved_complaint(
            citizen_id=None,
            complaint_id="CMP-APPROX001",
            location_source="ward_approximate",
        )

        cluster_id = services.assign_cluster(self.db, [1.0, 0.0], 19.076, 72.878)
        citizen_pin_cluster_id = services.assign_citizen_pinned_cluster(self.db, [1.0, 0.0], 19.076, 72.878)

        self.assertEqual(cluster_id, 2)
        self.assertEqual(citizen_pin_cluster_id, 2)
        self.assertEqual(self.client.get("/clusters", headers=self.staff_headers).json(), [])
        self.assertEqual(self.client.get("/clusters/1", headers=self.staff_headers).status_code, 404)

    def test_staff_location_retry_clusters_only_a_verified_geocoded_result(self):
        complaint = self._saved_complaint(
            citizen_id=None, complaint_id="CMP-RETRYLOC01", lat=None, lng=None,
            location_source="none", embedding=[1.0, 0.0],
        )
        complaint.status = "open"
        complaint.review_state = "accepted"
        complaint.cluster_id = -1
        self.db.commit()

        with patch.object(services, "resolve_location", return_value={
            "lat": 19.076, "lng": 72.878, "display_name": "Verified place, Mumbai", "source": "geoapify",
        }):
            response = self.client.post(
                f"/bmc/complaints/{complaint.id}/resolve-location", headers=self.staff_headers,
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["location_retry"], "resolved")
        self.assertEqual(response.json()["location_confidence"], "verified")
        self.assertEqual(response.json()["cluster_id"], 1)

    def test_staff_location_retry_does_not_promote_midpoint_to_verified_or_clustered(self):
        complaint = self._saved_complaint(
            citizen_id=None, complaint_id="CMP-RETRYMID01", lat=None, lng=None,
            location_source="none", embedding=[1.0, 0.0],
        )
        complaint.status = "open"
        complaint.review_state = "accepted"
        complaint.cluster_id = -1
        self.db.commit()

        with patch.object(services, "resolve_location", return_value={
            "lat": 19.12, "lng": 72.90, "display_name": "Approximate midpoint", "source": "geoapify_midpoint",
        }):
            response = self.client.post(
                f"/bmc/complaints/{complaint.id}/resolve-location", headers=self.staff_headers,
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["location_retry"], "unresolved")
        self.assertEqual(response.json()["cluster_id"], -1)
        self.assertIsNone(self.db.get(Complaint, complaint.id).lat)
        self.assertIsNone(self.db.get(Complaint, complaint.id).lng)

    def test_staff_location_retry_never_overwrites_verified_user_pin(self):
        complaint = self._saved_complaint(
            citizen_id=None, complaint_id="CMP-RETRYPIN01", lat=19.08, lng=72.88,
            location_source="citizen_pin", embedding=[1.0, 0.0],
        )
        original = (complaint.lat, complaint.lng)

        with patch.object(services, "resolve_location") as resolver:
            response = self.client.post(
                f"/bmc/complaints/{complaint.id}/resolve-location", headers=self.staff_headers,
            )

        resolver.assert_not_called()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["location_retry"], "already_verified")
        self.assertEqual((response.json()["lat"], response.json()["lng"]), original)

    def test_staff_location_retry_does_not_recheck_verified_geocoded_locations(self):
        for source in ("geoapify", "nominatim"):
            with self.subTest(source=source):
                complaint = self._saved_complaint(
                    citizen_id=None, complaint_id=f"CMP-VERIFIED-{source}", lat=19.12, lng=72.84,
                    location_source=source, embedding=[1.0, 0.0],
                )
                original = (complaint.lat, complaint.lng, complaint.location_source, complaint.cluster_id)

                with patch.object(services, "resolve_location") as resolver:
                    response = self.client.post(
                        f"/bmc/complaints/{complaint.id}/resolve-location", headers=self.staff_headers,
                    )

                resolver.assert_not_called()
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["location_retry"], "already_verified")
                saved = self.db.get(Complaint, complaint.id)
                self.assertEqual((saved.lat, saved.lng, saved.location_source, saved.cluster_id), original)

    def test_staff_approximation_layer_labels_ward_and_midpoint_markers_separately(self):
        ward = self._saved_complaint(
            citizen_id=None, complaint_id="CMP-WARDAPP01", location_source="ward_approximate",
        )
        midpoint = self._saved_complaint(
            citizen_id=None, complaint_id="CMP-MIDPOINT1", location_source="geoapify_midpoint",
        )
        midpoint.location = "Approximate midpoint between a library and a market"
        unknown = self._saved_complaint(
            citizen_id=None, complaint_id="CMP-UNKNOWN01", location_source="nominatim",
        )
        unknown.location_source = None
        self.db.commit()

        response = self.client.get("/map-approximations", headers=self.staff_headers)

        self.assertEqual(response.status_code, 200)
        markers = {item["id"]: item for item in response.json()}
        self.assertEqual(set(markers), {ward.id, midpoint.id})
        self.assertEqual(markers[ward.id]["location_confidence"], "approximate")
        self.assertIn("ward-centre", markers[ward.id]["marker_label"])
        self.assertIn("midpoint", markers[midpoint.id]["marker_label"])
        self.assertNotIn("centroid", markers[midpoint.id])
        self.assertNotIn(unknown.id, markers)
        self.assertEqual(self.client.get("/map-approximations").status_code, 401)
        self.assertEqual(self.client.get("/map-approximations", headers=self.citizen_headers).status_code, 403)

    def test_unknown_legacy_coordinates_are_returned_as_unresolved_without_data_changes(self):
        complaint = self._saved_complaint(
            citizen_id=None, complaint_id="CMP-LEGACYLOC1", location_source="nominatim",
        )
        original_coordinates = (complaint.lat, complaint.lng)
        complaint.location_source = None
        self.db.commit()

        response = self.client.get("/complaints", headers=self.staff_headers)

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json()["items"] if item["id"] == complaint.id)
        self.assertEqual(row["location_confidence"], "unknown")
        self.assertIsNone(row["lat"])
        self.assertIsNone(row["lng"])
        self.assertEqual((self.db.get(Complaint, complaint.id).lat, self.db.get(Complaint, complaint.id).lng), original_coordinates)
        self.assertEqual(self.client.get("/clusters", headers=self.staff_headers).json(), [])
        self.assertEqual(self.client.get("/clusters/1", headers=self.staff_headers).status_code, 404)

    def test_midpoint_submission_keeps_coordinates_as_approximate_and_unclustered(self):
        with patch.object(services, "get_infer", return_value=FakeInference()), patch.object(
            services, "resolve_location", return_value={
                "lat": 19.12, "lng": 72.90, "display_name": "Approximate midpoint between two places",
                "source": "geoapify_midpoint",
            },
        ):
            response = self.client.post(
                "/complaints", headers=self.staff_headers,
                json={"text": "A pothole between the library and market"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["cluster_id"], -1)
        self.assertEqual(result["location_confidence"], "approximate")
        self.assertEqual((result["lat"], result["lng"]), (19.12, 72.90))
        self.assertEqual(self.client.get("/clusters", headers=self.staff_headers).json(), [])
        self.assertEqual(
            [marker["id"] for marker in self.client.get("/map-approximations", headers=self.staff_headers).json()],
            [result["id"]],
        )

    def test_accepting_midpoint_report_keeps_it_approximate_and_unclustered(self):
        complaint = self._saved_complaint(
            citizen_id=None, complaint_id="CMP-MIDPEND01", location_source="nominatim_midpoint",
        )
        complaint.source = "civicsocial"
        complaint.review_state = "pending"
        complaint.cluster_id = -1
        self.db.commit()

        response = self.client.post(
            f"/bmc/reviews/{complaint.id}/accept", headers=self.staff_headers,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["cluster_id"], -1)
        self.assertEqual(response.json()["location_confidence"], "approximate")
        self.assertEqual(self.client.get("/clusters", headers=self.staff_headers).json(), [])
        self.assertEqual(
            self.client.get("/map-approximations", headers=self.staff_headers).json()[0]["id"],
            complaint.id,
        )

    def test_nonfinite_coordinates_do_not_join_or_create_map_clusters(self):
        self._saved_complaint(
            citizen_id=None,
            complaint_id="CMP-NANLOC01",
            lat=float("nan"),
            lng=72.878,
            location_source="nominatim",
        )

        cluster_id = services.assign_cluster(self.db, [1.0, 0.0], 19.076, 72.878)

        self.assertEqual(cluster_id, 2)
        self.assertEqual(self.client.get("/clusters", headers=self.staff_headers).json(), [])

    def test_unlocated_report_never_inherits_an_existing_cluster_map_pin(self):
        self._saved_complaint(citizen_id=None, complaint_id="CMP-CLUSTER1")
        cluster_id = services.assign_cluster(self.db, [1.0, 0.0], None, None)
        self.assertEqual(cluster_id, -1)

    def test_invalid_coordinates_and_embeddings_never_create_cluster_membership(self):
        self.assertEqual(services.assign_cluster(self.db, [1.0, 0.0], float("nan"), 72.878), -1)
        self.assertEqual(services.assign_cluster(self.db, [0.0, 0.0], 19.076, 72.878), -1)
        self.assertEqual(services.assign_cluster(self.db, [float("inf"), 1.0], 19.076, 72.878), -1)

        invalid_member = self._saved_complaint(
            citizen_id=None, complaint_id="CMP-BADVECTOR1", embedding=[0.0, 0.0],
        )
        invalid_member.cluster_id = 1
        self.db.commit()
        self.assertEqual(self.client.get("/clusters", headers=self.staff_headers).json(), [])
        self.assertEqual(self.client.get("/clusters/1", headers=self.staff_headers).status_code, 404)

    def test_related_mittal_fixture_groups_but_unrelated_school_and_vihar_fixture_stays_separate(self):
        anchor = self._saved_complaint(citizen_id=None, complaint_id="CMP-MITTAL001")
        anchor.text = "Poor road condition near Mittal College in Malad West"
        anchor.location = "Mittal College, Malad West"
        self.db.commit()
        near_location = services.assign_cluster(self.db, [0.6, 0.8], 19.076 + 25 / 111_195, 72.878)
        self.assertEqual(near_location, 1)

        far_location = services.assign_cluster(self.db, [1.0, 0.0], 19.076 + 3_500 / 111_195, 72.878)
        self.assertEqual(far_location, 2)

    def test_fatima_and_nausena_fixture_stays_separate_despite_shared_locality(self):
        footpath = self._saved_complaint(
            citizen_id=None, complaint_id="CMP-FATIMA001", embedding=[1.0, 0.0],
        )
        footpath.text = "Footpath encroachment outside Fatima High School, Vidyavihar West"
        garbage_text = "Garbage collection outside Nausena Vihar, Vidyavihar West"
        self.db.commit()

        self.assertIn("Nausena Vihar", garbage_text)
        cluster_id = services.assign_cluster(self.db, [0.0, 1.0], 19.076 + 500 / 111_195, 72.878)

        self.assertEqual(cluster_id, 2)

    def test_located_report_does_not_join_a_cluster_without_member_coordinates(self):
        self._saved_complaint(citizen_id=None, complaint_id="CMP-CLUSTER1", lat=None, lng=None)
        cluster_id = services.assign_cluster(self.db, [1.0, 0.0], 19.076, 72.878)
        self.assertEqual(cluster_id, 2)

    def test_staff_map_never_turns_unresolved_complaint_into_a_fabricated_pin(self):
        unresolved = self._saved_complaint(
            citizen_id=None, complaint_id="CMP-UNLOCATED1", lat=None, lng=None,
        )
        unresolved.cluster_id = 1
        unresolved.review_state = "accepted"
        self.db.commit()
        self.assertEqual(self.client.get("/clusters", headers=self.staff_headers).json(), [])
        self.assertEqual(self.client.get("/clusters/1", headers=self.staff_headers).status_code, 404)

    def test_legacy_missing_category_is_inferred_for_map_without_rewriting_record(self):
        complaint = self._saved_complaint(citizen_id=None, complaint_id="CMP-CATMAP01")
        complaint.text = "A large pothole has formed on the road"
        complaint.category = None
        self.db.commit()

        response = self.client.get("/clusters", headers=self.staff_headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["dominant_category"], "Roads")
        self.assertEqual(response.json()[0]["top_ranked_complaint"]["category"], "Roads")
        self.assertIsNone(self.db.get(Complaint, complaint.id).category)

    def test_list_and_media_are_owner_scoped_and_lists_omit_payloads(self):
        complaint = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-MEDIA001")
        complaint.image_data = "data:image/jpeg;base64,cGhvdG8="
        complaint.video_data = "data:video/mp4;base64,dmlkZW8="
        complaint.has_video = True
        complaint.image_url = "https://images.example/citizen-private.jpg"
        self.db.commit()
        self._saved_complaint(citizen_id=self.other.id, complaint_id="CMP-OTHER001")

        rows = self.client.get("/citizen/complaints", headers=self.citizen_headers)
        self.assertEqual(rows.status_code, 200)
        self.assertEqual([item["id"] for item in rows.json()["items"]], [complaint.id])
        self.assertNotIn("video_data", rows.json()["items"][0])
        self.assertNotIn("image_data", rows.json()["items"][0])

        own_detail = self.client.get(f"/citizen/complaints/{complaint.id}", headers=self.citizen_headers)
        self.assertEqual(own_detail.status_code, 200)
        self.assertEqual(own_detail.json()["status"], "open")
        own_media = self.client.get(f"/citizen/complaints/{complaint.id}/media", headers=self.citizen_headers)
        self.assertEqual(own_media.status_code, 200)
        self.assertEqual(own_media.json()["video_data"], complaint.video_data)
        self.assertNotIn("image_url", own_media.json())
        self.assertNotIn("images.example", own_media.text)
        other_media = self.client.get(f"/citizen/complaints/{complaint.id}/media", headers=self.other_headers)
        other_detail = self.client.get(f"/citizen/complaints/{complaint.id}", headers=self.other_headers)
        self.assertEqual(other_media.status_code, 404)
        self.assertEqual(other_detail.status_code, 404)

    def test_supported_image_persists_through_shared_pipeline(self):
        image = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffimage-bytes").decode()
        with patch.object(services, "get_infer", return_value=FakeInference()), patch.object(
            services, "resolve_location", return_value={"lat": None, "lng": None, "display_name": None, "source": "none"}
        ):
            response = self.client.post(
                "/citizen/complaints", headers=self.citizen_headers,
                json={"text": "A pothole by my street", "image_base64": image},
            )
        self.assertEqual(response.status_code, 201, response.text)
        saved = self.db.get(Complaint, response.json()["id"])
        self.assertEqual(saved.image_data, image)
        self.assertTrue(saved.has_media)
        self.assertEqual(saved.photo_severity, "High")

    def test_supported_video_persists_original_and_transcript(self):
        raw_video = b"\x00\x00\x00\x10ftypmp42\x00\x00\x00\x00video-data"
        video = "data:video/mp4;base64," + base64.b64encode(raw_video).decode()
        with patch.object(services, "get_infer", return_value=FakeInference()), patch.object(
            services, "resolve_location", return_value={"lat": None, "lng": None, "display_name": None, "source": "none"}
        ):
            response = self.client.post(
                "/citizen/complaints", headers=self.citizen_headers,
                json={"text": "A pothole by my street", "video_base64": video},
            )
        self.assertEqual(response.status_code, 201, response.text)
        saved = self.db.get(Complaint, response.json()["id"])
        self.assertEqual(saved.video_data, video)
        self.assertTrue(saved.has_video)
        self.assertEqual(saved.transcript, "transcribed words")

    def test_invalid_media_types_sizes_and_coordinates_are_rejected(self):
        bad_type = "data:text/plain;base64," + base64.b64encode(b"not an image").decode()
        valid_jpeg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffimage").decode()
        valid_mp4 = "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x10ftypmp42\x00\x00\x00\x00").decode()
        valid_quicktime = "data:video/quicktime;base64," + base64.b64encode(b"\x00\x00\x00\x10ftypqt  \x00\x00\x00\x00").decode()
        valid_webm = "data:video/webm;base64," + base64.b64encode(b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm").decode()
        cases = [
            ({"image_base64": bad_type}, 422),
            ({"image_base64": "data:image/jpeg;base64,%%%"}, 422),
            ({"image_base64": "data:image/jpeg;base64," + base64.b64encode(b"not-a-real-image").decode()}, 422),
            ({"image_base64": "data:image/jpeg;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode()}, 422),
            ({"video_base64": "data:video/mp4;base64," + base64.b64encode(b"not-a-real-video").decode()}, 422),
            ({"video_base64": "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x10ftypqt  \x00\x00\x00\x00").decode()}, 422),
            ({"video_base64": "data:video/quicktime;base64," + base64.b64encode(b"\x00\x00\x00\x10ftypmp42\x00\x00\x00\x00").decode()}, 422),
            ({"video_base64": "data:video/webm;base64," + base64.b64encode(b"\x1a\x45\xdf\xa3\x87\x42\x82\x84mkvx").decode()}, 422),
            ({"lat": 19.076}, 422),
            ({"lat": 40.0, "lng": 72.878}, 422),
        ]
        for fields, expected in cases:
            response = self.client.post(
                "/citizen/complaints", headers=self.citizen_headers,
                json={"text": "A pothole by my street", **fields},
            )
            self.assertEqual(response.status_code, expected, response.text)

        with patch.object(main, "MAX_SUBMISSION_IMAGE_BYTES", 2):
            too_large_image = self.client.post(
                "/citizen/complaints", headers=self.citizen_headers,
                json={"text": "A pothole by my street", "image_base64": valid_jpeg},
            )
        self.assertEqual(too_large_image.status_code, 422)
        with patch.object(main, "MAX_SUBMISSION_VIDEO_BYTES", 2):
            too_large_video = self.client.post(
                "/citizen/complaints", headers=self.citizen_headers,
                json={"text": "A pothole by my street", "video_base64": valid_mp4},
            )
        self.assertEqual(too_large_video.status_code, 422)

        valid_media = main.CitizenSubmission.model_validate({
            "text": "A pothole by my street",
            "image_base64": "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode(),
            "video_base64": valid_mp4,
        })
        self.assertTrue(valid_media.image_base64)
        self.assertTrue(valid_media.video_base64)
        self.assertTrue(main.CitizenSubmission.model_validate({
            "text": "A pothole by my street",
            "image_base64": "data:image/gif;base64," + base64.b64encode(b"GIF89aimage").decode(),
            "video_base64": valid_quicktime,
        }))
        self.assertTrue(main.CitizenSubmission.model_validate({
            "text": "A pothole by my street",
            "image_base64": "data:image/webp;base64," + base64.b64encode(b"RIFF0000WEBPdata").decode(),
            "video_base64": valid_webm,
        }))

    def test_combined_citizen_media_persists_originals_and_uses_more_severe_assessment(self):
        image = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xfforiginal-photo").decode()
        video = "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x10ftypmp42\x00\x00\x00\x00original-video").decode()
        inference = FakeInference()
        with patch.object(services, "get_infer", return_value=inference), patch.object(
            services, "resolve_location", return_value={"lat": None, "lng": None, "display_name": None, "source": "none"}
        ):
            response = self.client.post(
                "/citizen/complaints", headers=self.citizen_headers,
                json={"text": "A pothole by my street", "image_base64": image, "video_base64": video},
            )
        self.assertEqual(response.status_code, 201, response.text)
        saved = self.db.get(Complaint, response.json()["id"])
        self.assertEqual(inference.scored_images, [image])
        self.assertEqual(inference.processed_videos, [video])
        self.assertEqual(saved.uploaded_photo_data, image)
        self.assertEqual(saved.video_data, video)
        self.assertEqual(saved.image_data, "data:image/jpeg;base64," + base64.b64encode(b"representative-frame").decode())
        self.assertEqual(saved.photo_severity, "High")
        self.assertAlmostEqual(saved.final_score, 2.55)
        self.assertEqual(saved.final_severity, "High")
        own_media = self.client.get(
            f"/citizen/complaints/{saved.id}/media", headers=self.citizen_headers
        )
        self.assertEqual(own_media.json()["uploaded_photo_data"], image)
        self.assertEqual(own_media.json()["video_data"], video)
        self.assertEqual(own_media.json()["image_data"], saved.image_data)

    def test_combined_media_uses_more_severe_video_when_photo_is_less_severe(self):
        image = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xfforiginal-photo").decode()
        video = "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x10ftypmp42\x00\x00\x00\x00original-video").decode()
        inference = FakeInference(photo_result="Low", video_result="High")
        with patch.object(services, "get_infer", return_value=inference), patch.object(
            services, "resolve_location", return_value={"lat": None, "lng": None, "display_name": None, "source": "none"}
        ):
            response = self.client.post(
                "/citizen/complaints", headers=self.citizen_headers,
                json={"text": "A pothole by my street", "image_base64": image, "video_base64": video},
            )
        self.assertEqual(response.status_code, 201, response.text)
        saved = self.db.get(Complaint, response.json()["id"])
        self.assertEqual(inference.scored_images, [image])
        self.assertEqual(inference.processed_videos, [video])
        self.assertEqual(saved.photo_severity, "High")
        self.assertAlmostEqual(saved.final_score, 2.55)
        self.assertEqual(saved.final_severity, "High")

    def test_combined_bmc_media_uses_shared_pipeline_and_appears_in_cluster_detail(self):
        image = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xfforiginal-photo").decode()
        video = "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x10ftypmp42\x00\x00\x00\x00original-video").decode()
        inference = FakeInference()
        with patch.object(services, "get_infer", return_value=inference), patch.object(
            services, "resolve_location", return_value={"lat": 19.076, "lng": 72.878, "display_name": "Mumbai", "source": "nominatim"}
        ):
            response = self.client.post(
                "/complaints", headers=self.staff_headers,
                json={"text": "A pothole by my street", "image_base64": image, "video_base64": video}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(inference.scored_images, [image])
        self.assertEqual(inference.processed_videos, [video])
        for private_field in ("image_data", "display_image", "uploaded_photo_data", "video_data", "image_url", "transcript"):
            self.assertNotIn(private_field, response.json())
        self.assertNotIn(image, response.text)
        self.assertNotIn(video, response.text)
        complaint = self.db.get(Complaint, response.json()["id"])
        self.assertEqual(complaint.uploaded_photo_data, image)
        self.assertEqual(complaint.video_data, video)
        cluster_response = self.client.get(f"/clusters/{complaint.cluster_id}", headers=self.staff_headers)
        self.assertEqual(cluster_response.status_code, 200)
        report = next(item for item in cluster_response.json() if item["id"] == complaint.id)
        for private_field in ("image_data", "display_image", "uploaded_photo_data", "video_data", "image_url", "transcript"):
            self.assertNotIn(private_field, report)
        self.assertTrue(report["has_media"])
        self.assertTrue(report["has_video"])
        self.assertNotIn(image, cluster_response.text)
        self.assertNotIn(video, cluster_response.text)
        staff_media = self.client.get(
            f"/bmc/complaints/{complaint.id}/media", headers=self.staff_headers
        )
        self.assertEqual(staff_media.status_code, 200)
        self.assertEqual(staff_media.json()["uploaded_photo_data"], image)
        self.assertEqual(staff_media.json()["video_data"], video)
        self.assertEqual(staff_media.json()["representative_frame_data"], complaint.image_data)
        self.assertEqual(staff_media.json()["transcript"], complaint.transcript)

    def test_staff_complaint_and_cluster_apis_never_include_evidence_payloads(self):
        complaint = self._saved_complaint(citizen_id=None, complaint_id="CMP-PUBLIC01")
        complaint.image_data = "data:image/jpeg;base64,PRIVATE_PHOTO_PAYLOAD"
        complaint.uploaded_photo_data = "data:image/jpeg;base64,PRIVATE_ORIGINAL_PHOTO"
        complaint.video_data = "data:video/mp4;base64,PRIVATE_VIDEO_PAYLOAD"
        complaint.transcript = "PRIVATE TRANSCRIPT TEXT"
        complaint.has_media = True
        complaint.has_video = True
        complaint.image_url = "https://private.example/evidence.jpg"
        self.db.commit()

        for path in ("/complaints", "/clusters", f"/clusters/{complaint.cluster_id}"):
            response = self.client.get(path, headers=self.staff_headers)
            self.assertEqual(response.status_code, 200, response.text)
            serialized = response.json()
            self.assertNotIn("PRIVATE_PHOTO_PAYLOAD", response.text)
            self.assertNotIn("PRIVATE_ORIGINAL_PHOTO", response.text)
            self.assertNotIn("PRIVATE_VIDEO_PAYLOAD", response.text)
            self.assertNotIn("PRIVATE TRANSCRIPT TEXT", response.text)
            self.assertNotIn("private.example", response.text)
            reports = serialized["items"] if path == "/complaints" else serialized
            if path == "/clusters":
                reports = [cluster["top_ranked_complaint"] for cluster in serialized]
            for report in reports:
                if report["id"] == complaint.id:
                    for private_field in ("image_data", "display_image", "uploaded_photo_data", "video_data", "image_url", "transcript"):
                        self.assertNotIn(private_field, report)
                    self.assertTrue(report["has_media"])
                    self.assertTrue(report["has_video"])

    def test_bmc_media_endpoint_requires_staff_and_returns_404_for_missing_complaint(self):
        complaint = self._saved_complaint(citizen_id=None, complaint_id="CMP-MEDIA002")
        complaint.image_data = "data:image/jpeg;base64,cGhvdG8="
        complaint.has_media = True
        self.db.commit()

        anonymous = self.client.get(f"/bmc/complaints/{complaint.id}/media")
        citizen = self.client.get(
            f"/bmc/complaints/{complaint.id}/media", headers=self.citizen_headers
        )
        staff = self.client.get(
            f"/bmc/complaints/{complaint.id}/media", headers=self.staff_headers
        )
        missing = self.client.get("/bmc/complaints/CMP-MISSING/media", headers=self.staff_headers)
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(citizen.status_code, 403)
        self.assertEqual(staff.status_code, 200)
        self.assertEqual(staff.json()["uploaded_photo_data"], complaint.image_data)
        self.assertEqual(missing.status_code, 404)

    def test_bmc_staff_media_endpoint_supports_photo_video_and_combined_legacy_storage(self):
        photo = self._saved_complaint(citizen_id=None, complaint_id="CMP-PHOTO001")
        photo.image_data = "data:image/jpeg;base64,PHOTO"
        photo.has_media = True
        video = self._saved_complaint(citizen_id=None, complaint_id="CMP-VIDEO001")
        video.image_data = "data:image/jpeg;base64,FRAME"
        video.video_data = "data:video/mp4;base64,VIDEO"
        video.transcript = "transcript"
        video.has_video = video.has_media = True
        combined = self._saved_complaint(citizen_id=None, complaint_id="CMP-COMBINED1")
        combined.image_data = "data:image/jpeg;base64,FRAME2"
        combined.uploaded_photo_data = "data:image/jpeg;base64,PHOTO2"
        combined.video_data = "data:video/mp4;base64,VIDEO2"
        combined.has_video = combined.has_media = True
        legacy = self._saved_complaint(citizen_id=None, complaint_id="CMP-LEGACY01")
        legacy.image_url = "https://images.example/legacy.jpg"
        legacy.has_media = True
        self.db.commit()

        photo_media = self.client.get(f"/bmc/complaints/{photo.id}/media", headers=self.staff_headers).json()
        video_media = self.client.get(f"/bmc/complaints/{video.id}/media", headers=self.staff_headers).json()
        combined_media = self.client.get(f"/bmc/complaints/{combined.id}/media", headers=self.staff_headers).json()
        legacy_response = self.client.get(f"/bmc/complaints/{legacy.id}/media", headers=self.staff_headers)
        legacy_media = legacy_response.json()
        self.assertEqual(photo_media["uploaded_photo_data"], photo.image_data)
        self.assertIsNone(photo_media["video_data"])
        self.assertIsNone(video_media["uploaded_photo_data"])
        self.assertEqual(video_media["video_data"], video.video_data)
        self.assertEqual(video_media["representative_frame_data"], video.image_data)
        self.assertEqual(combined_media["uploaded_photo_data"], combined.uploaded_photo_data)
        self.assertEqual(combined_media["video_data"], combined.video_data)
        self.assertEqual(combined_media["representative_frame_data"], combined.image_data)
        self.assertNotIn("image_url", legacy_media)
        self.assertTrue(legacy_media["legacy_external_media_unavailable"])
        self.assertNotIn("images.example", legacy_response.text)
        self.assertEqual(self.client.get("/static/legacy.jpg").status_code, 404)
        self.assertEqual(self.client.get("/artifacts/legacy.jpg").status_code, 404)

    def test_legacy_video_data_overrides_false_has_video_flag(self):
        complaint = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-LEGACYV1")
        complaint.image_data = "data:image/jpeg;base64,FRAME"
        complaint.video_data = "data:video/mp4;base64,VIDEO"
        complaint.has_video = False
        self.db.commit()
        public_response = self.client.get("/complaints", headers=self.staff_headers)
        public_item = next(row for row in public_response.json()["items"] if row["id"] == complaint.id)
        citizen_item = self.client.get("/citizen/complaints", headers=self.citizen_headers).json()["items"][0]
        staff_media = self.client.get(
            f"/bmc/complaints/{complaint.id}/media", headers=self.staff_headers
        ).json()
        self.assertTrue(public_item["has_video"])
        self.assertFalse(public_item["has_uploaded_photo"])
        self.assertTrue(citizen_item["has_video"])
        self.assertFalse(citizen_item["has_uploaded_photo"])
        self.assertTrue(staff_media["has_video"])
        self.assertIsNone(staff_media["uploaded_photo_data"])
        self.assertEqual(staff_media["representative_frame_data"], complaint.image_data)

    def test_resolution_evidence_routes_are_removed(self):
        complaint = self._saved_complaint(citizen_id=None, complaint_id="CMP-NORESOLUTION")
        upload = self.client.post(
            f"/complaints/{complaint.id}/resolve", headers=self.staff_headers,
            json={"image_base64": "data:image/jpeg;base64,/9j/"},
        )
        confirm = self.client.post(
            f"/complaints/{complaint.id}/resolve/1/confirm", headers=self.staff_headers,
        )
        media = self.client.get(
            f"/bmc/complaints/{complaint.id}/resolutions/1/media", headers=self.staff_headers,
        )
        self.assertEqual(upload.status_code, 404)
        self.assertEqual(confirm.status_code, 404)
        self.assertEqual(media.status_code, 404)


    def test_bmc_photo_only_submission_uses_shared_pipeline(self):
        image = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffphoto").decode()
        inference = FakeInference()
        with patch.object(services, "get_infer", return_value=inference), patch.object(
            services, "resolve_location", return_value={"lat": None, "lng": None, "display_name": None, "source": "none"}
        ):
            response = self.client.post("/complaints", headers=self.staff_headers, json={"text": "A pothole by my street", "image_base64": image})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(image, response.text)
        for private_field in ("image_data", "display_image", "uploaded_photo_data", "video_data", "image_url", "transcript"):
            self.assertNotIn(private_field, response.json())
        saved = self.db.get(Complaint, response.json()["id"])
        self.assertEqual(inference.scored_images, [image])
        self.assertEqual(inference.processed_videos, [])
        self.assertEqual(saved.image_data, image)
        self.assertIsNone(saved.video_data)

    def test_bmc_video_only_submission_uses_shared_pipeline(self):
        video = "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x10ftypmp42\x00\x00\x00\x00video").decode()
        inference = FakeInference()
        with patch.object(services, "get_infer", return_value=inference), patch.object(
            services, "resolve_location", return_value={"lat": None, "lng": None, "display_name": None, "source": "none"}
        ):
            response = self.client.post("/complaints", headers=self.staff_headers, json={"text": "A pothole by my street", "video_base64": video})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(video, response.text)
        for private_field in ("image_data", "display_image", "uploaded_photo_data", "video_data", "image_url", "transcript"):
            self.assertNotIn(private_field, response.json())
        saved = self.db.get(Complaint, response.json()["id"])
        self.assertEqual(inference.scored_images, [])
        self.assertEqual(inference.processed_videos, [video])
        self.assertEqual(saved.video_data, video)
        self.assertTrue(saved.image_data.startswith("data:image/jpeg;base64,"))

    def test_bmc_rejects_mismatched_mime_and_oversized_media(self):
        mismatched_image = "data:image/png;base64," + base64.b64encode(b"\xff\xd8\xffjpeg").decode()
        rejected = self.client.post(
            "/complaints", headers=self.staff_headers,
            json={"text": "A pothole by my street", "image_base64": mismatched_image}
        )
        self.assertEqual(rejected.status_code, 422)
        image = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffphoto").decode()
        video = "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x10ftypmp42\x00\x00\x00\x00video").decode()
        with patch.object(main, "MAX_SUBMISSION_IMAGE_BYTES", 2):
            oversized_photo = self.client.post(
                "/complaints", headers=self.staff_headers,
                json={"text": "A pothole by my street", "image_base64": image}
            )
        with patch.object(main, "MAX_SUBMISSION_VIDEO_BYTES", 2):
            oversized_video = self.client.post(
                "/complaints", headers=self.staff_headers,
                json={"text": "A pothole by my street", "video_base64": video}
            )
        self.assertEqual(oversized_photo.status_code, 422)
        self.assertEqual(oversized_video.status_code, 422)

    def test_citizen_request_limit_covers_maximum_photo_and_video_base64(self):
        photo_encoded = 4 * math.ceil(main.MAX_CITIZEN_IMAGE_BYTES / 3)
        video_encoded = 4 * math.ceil(main.MAX_CITIZEN_VIDEO_BYTES / 3)
        # Includes conservative JSON keys, values, and text overhead.
        self.assertLess(photo_encoded + video_encoded + 1024, main.MAX_CITIZEN_REQUEST_BYTES)

    def test_combined_upload_passes_through_bounded_reader_and_over_limit_is_rejected(self):
        image = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffphoto").decode()
        video = "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x10ftypmp42\x00\x00\x00\x00video").decode()
        body = json.dumps({
            "text": "A pothole by my street",
            "image_base64": image,
            "video_base64": video,
        }).encode()

        class StreamRequest:
            def __init__(self, content, declared_size=None):
                self.content = content
                self.headers = {} if declared_size is None else {"content-length": str(declared_size)}

            async def stream(self):
                yield self.content[:17]
                yield self.content[17:]

        with patch.object(main, "MAX_SUBMISSION_IMAGE_BYTES", 64), patch.object(
            main, "MAX_SUBMISSION_VIDEO_BYTES", 64
        ), patch.object(main, "MAX_SUBMISSION_REQUEST_BYTES", len(body)):
            payload = asyncio.run(main._read_submission(StreamRequest(body, len(body)), main.Submission, len(body)))
            self.assertEqual(payload.image_base64, image)
            self.assertEqual(payload.video_base64, video)
        with patch.object(main, "MAX_SUBMISSION_IMAGE_BYTES", 64), patch.object(
            main, "MAX_SUBMISSION_VIDEO_BYTES", 64
        ), patch.object(main, "MAX_SUBMISSION_REQUEST_BYTES", len(body) - 1):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(main._read_submission(StreamRequest(body), main.Submission, len(body) - 1))
        self.assertEqual(raised.exception.status_code, 413)

    def test_pending_citizen_submission_stays_out_of_active_bmc_feed(self):
        with patch.object(services, "get_infer", return_value=FakeInference()), patch.object(
            services, "resolve_location", return_value={"lat": None, "lng": None, "display_name": None, "source": "none"}
        ), patch.object(services, "assign_cluster", wraps=services.assign_cluster) as legacy_cluster:
            submitted = self.client.post(
                "/citizen/complaints", headers=self.citizen_headers,
                json={"text": "A pothole by my street"},
            )
        self.assertEqual(submitted.status_code, 201, submitted.text)
        legacy_cluster.assert_not_called()
        feed = self.client.get("/complaints", headers=self.staff_headers)
        self.assertEqual(feed.status_code, 200)
        self.assertNotIn(submitted.json()["id"], [item["id"] for item in feed.json()["items"]])
        self.assertEqual(submitted.json()["status"], "pending_review")
        self.assertEqual(self.client.get("/clusters", headers=self.staff_headers).json(), [])

    def test_citizen_search_dashboard_and_status_history_are_owner_scoped(self):
        own = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-SEARCH01")
        own.category = "Roads"; own.ward_code = "A"; own.location = "Vidyavihar"; own.status = "in_progress"
        other = self._saved_complaint(citizen_id=self.other.id, complaint_id="CMP-SEARCH02")
        other.text = "Water leak at market"; other.category = "Water"
        self.db.commit()
        result = self.client.get("/citizen/complaints?q=Vidyavihar&status=in_progress&category=Roads", headers=self.citizen_headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual([row["id"] for row in result.json()["items"]], [own.id])
        self.assertEqual(result.json()["total"], 1)
        self.assertEqual(self.client.get("/citizen/complaints?q=Water", headers=self.citizen_headers).json()["items"], [])
        dashboard = self.client.get("/citizen/dashboard", headers=self.citizen_headers)
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.json()["total"], 1)
        self.assertEqual(dashboard.json()["active"], 1)
        self.assertEqual(dashboard.json()["resolved"], 0)
        hidden = self.client.get(f"/citizen/complaints/{other.id}", headers=self.citizen_headers)
        self.assertEqual(hidden.status_code, 404)

    def test_citizen_category_filter_uses_canonical_stored_category(self):
        own = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-CATEGORY1")
        own.category = "Roads"
        self.db.commit()
        all_rows = self.client.get("/citizen/complaints", headers=self.citizen_headers)
        self.assertIn("Roads", all_rows.json()["categories"])
        filtered = self.client.get("/citizen/complaints?category=Roads", headers=self.citizen_headers)
        self.assertEqual([row["id"] for row in filtered.json()["items"]], [own.id])
        self.assertEqual(filtered.json()["categories"], ["Roads"])

    def test_citizen_status_filters_use_effective_review_and_work_status(self):
        statuses = ("open", "inspection_scheduled", "contractor_allotted", "in_progress", "resolved")
        expected = {}
        for index, status in enumerate(statuses):
            complaint = self._saved_complaint(citizen_id=self.citizen.id, complaint_id=f"CMP-FILTER{index}")
            complaint.status = status
            complaint.review_state = "accepted"
            expected[status] = complaint.id
        pending = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-FILTERP")
        pending.status = "open"
        pending.review_state = "pending"
        pending.cluster_id = -1
        rejected = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-FILTERR")
        rejected.review_state = "rejected"
        self.db.commit()

        for status, complaint_id in expected.items():
            response = self.client.get(f"/citizen/complaints?status={status}", headers=self.citizen_headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([row["id"] for row in response.json()["items"]], [complaint_id], status)
        pending_rows = self.client.get("/citizen/complaints?status=pending_review", headers=self.citizen_headers).json()["items"]
        rejected_rows = self.client.get("/citizen/complaints?status=rejected", headers=self.citizen_headers).json()["items"]
        open_rows = self.client.get("/citizen/complaints?status=open", headers=self.citizen_headers).json()["items"]
        self.assertEqual([row["id"] for row in pending_rows], [pending.id])
        self.assertEqual([row["id"] for row in rejected_rows], [rejected.id])
        self.assertNotIn(pending.id, [row["id"] for row in open_rows])
        self.assertNotIn(rejected.id, [row["id"] for row in open_rows])

    def test_status_update_is_staff_only_validated_and_notifies_owned_citizen(self):
        complaint = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-STATUS01")
        anonymous = self.client.patch(f"/complaints/{complaint.id}/status", json={"status": "in_progress"})
        citizen = self.client.patch(f"/complaints/{complaint.id}/status", headers=self.citizen_headers, json={"status": "in_progress"})
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(citizen.status_code, 403)
        self.db.refresh(complaint)
        self.assertEqual(complaint.status, "open")
        invalid = self.client.patch(f"/complaints/{complaint.id}/status", headers=self.staff_headers, json={"status": "completed"})
        self.assertEqual(invalid.status_code, 409)
        invalid_value = self.client.patch(f"/complaints/{complaint.id}/status", headers=self.staff_headers, json={"status": "unknown"})
        self.assertEqual(invalid_value.status_code, 409)
        changed = self.client.patch(f"/complaints/{complaint.id}/status", headers=self.staff_headers, json={"status": "in_progress"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["status"], "in_progress")
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(complaint_id=complaint.id, event_type="status_changed").count(), 1)
        notices = self.client.get("/citizen/notifications", headers=self.citizen_headers)
        self.assertEqual(notices.status_code, 200)
        self.assertEqual(notices.json()["unread_count"], 1)
        mark = self.client.patch(f"/citizen/notifications/{notices.json()['items'][0]['id']}/read", headers=self.other_headers)
        self.assertEqual(mark.status_code, 404)
        own_mark = self.client.patch(f"/citizen/notifications/{notices.json()['items'][0]['id']}/read", headers=self.citizen_headers)
        self.assertEqual(own_mark.status_code, 200)
        self.assertEqual(own_mark.json()["unread_count"], 0)

    def test_staff_can_resolve_directly_and_citizen_gets_history_and_notification(self):
        complaint = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-DIRECTRESOLVE")
        response = self.client.patch(
            f"/complaints/{complaint.id}/status", headers=self.staff_headers,
            json={"status": "resolved"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "resolved")

        self.db.refresh(complaint)
        self.assertEqual(complaint.status, "resolved")
        history = self.db.query(ComplaintStatusHistory).filter_by(
            complaint_id=complaint.id, event_type="status_changed", to_status="resolved"
        ).one()
        self.assertEqual(history.from_status, "open")
        self.assertEqual(history.actor_user_id, self.staff.id)
        citizen_detail = self.client.get(
            f"/citizen/complaints/{complaint.id}", headers=self.citizen_headers
        )
        self.assertEqual(citizen_detail.status_code, 200)
        self.assertEqual(citizen_detail.json()["status"], "resolved")
        self.assertTrue(any(
            event["event_type"] == "status_changed" and event["to_status"] == "resolved"
            for event in citizen_detail.json()["history"]
        ))
        notices = self.client.get("/citizen/notifications", headers=self.citizen_headers)
        self.assertEqual(notices.status_code, 200)
        self.assertEqual(notices.json()["unread_count"], 1)
        self.assertEqual(notices.json()["items"][0]["title"], "Complaint status updated")

    def test_failed_status_transaction_rolls_back_status_history_and_notification(self):
        from sqlalchemy.exc import SQLAlchemyError

        complaint = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-STATUSFAIL")
        with patch("sqlalchemy.orm.Session.commit", side_effect=SQLAlchemyError("database unavailable")):
            response = self.client.patch(
                f"/complaints/{complaint.id}/status", headers=self.staff_headers,
                json={"status": "resolved"},
            )
        self.assertEqual(response.status_code, 500)
        self.db.refresh(complaint)
        self.assertEqual(complaint.status, "open")
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(complaint_id=complaint.id).count(), 0)
        self.assertEqual(self.client.get("/citizen/notifications", headers=self.citizen_headers).json()["unread_count"], 0)

    def test_submission_creates_deduplicated_initial_history_and_notifications(self):
        with patch.object(services, "get_infer", return_value=FakeInference()), patch.object(
            services, "resolve_location", return_value={"lat": None, "lng": None, "display_name": None, "source": "none"}
        ):
            response = self.client.post("/citizen/complaints", headers=self.citizen_headers, json={"text": "A pothole by my street"})
        self.assertEqual(response.status_code, 201, response.text)
        complaint_id = response.json()["id"]
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(event_key=f"complaint-submitted:{complaint_id}").count(), 1)
        received = self.db.query(Notification).filter_by(event_key=f"complaint-received:{complaint_id}").count()
        self.assertEqual(received, 1)
        complaint = self.db.get(Complaint, complaint_id)
        main._record_submission_events(self.db, complaint)
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(event_key=f"complaint-submitted:{complaint_id}").count(), 1)
        self.assertEqual(self.db.query(Notification).filter_by(event_key=f"complaint-received:{complaint_id}").count(), 1)
        self.assertEqual(self.client.get("/bmc/notifications", headers=self.staff_headers).json()["unread_count"], 1)
        mark_all = self.client.post("/bmc/notifications/read-all", headers=self.staff_headers)
        self.assertEqual(mark_all.status_code, 200)
        self.assertEqual(mark_all.json()["unread_count"], 0)

    def test_staff_notification_read_one_and_all_preserve_persisted_unread_count(self):
        first = Notification(
            recipient_user_id=self.staff.id, audience_role="admin", complaint_id=None,
            event_key="staff-read-test:first", title="First notice", summary="First summary", read_at=None,
        )
        second = Notification(
            recipient_user_id=self.staff.id, audience_role="admin", complaint_id=None,
            event_key="staff-read-test:second", title="Second notice", summary="Second summary", read_at=None,
        )
        self.db.add_all([first, second])
        self.db.commit()

        before = self.client.get("/bmc/notifications", headers=self.staff_headers)
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.json()["unread_count"], 2)
        self.assertIsNone(first.read_at, "fetching notification history must not mark it as read")
        self.assertEqual(self.client.get("/bmc/reviews", headers=self.staff_headers).json()["total"], 0)
        one = self.client.patch(f"/bmc/notifications/{first.id}/read", headers=self.staff_headers)
        self.assertEqual(one.status_code, 200, one.text)
        self.assertEqual(one.json()["unread_count"], 1)
        self.assertTrue(next(row for row in one.json()["items"] if row["id"] == first.id)["read"])
        self.assertFalse(next(row for row in one.json()["items"] if row["id"] == second.id)["read"])
        # A fresh GET represents the state a page reload or navigation sees.
        reloaded = self.client.get("/bmc/notifications", headers=self.staff_headers)
        self.assertEqual(reloaded.json()["unread_count"], 1)
        all_read = self.client.post("/bmc/notifications/read-all", headers=self.staff_headers)
        self.assertEqual(all_read.status_code, 200)
        self.assertEqual(all_read.json()["unread_count"], 0)
        self.assertEqual(self.client.get("/bmc/notifications", headers=self.staff_headers).json()["unread_count"], 0)

    def test_new_submission_response_uses_pending_review_at_both_levels(self):
        with patch.object(services, "get_infer", return_value=FakeInference()), patch.object(
            services, "resolve_location", return_value={"lat": None, "lng": None, "display_name": None, "source": "none"}
        ):
            response = self.client.post("/citizen/complaints", headers=self.citizen_headers, json={"text": "A pothole by my street"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["status"], "pending_review")
        self.assertEqual(response.json()["complaint"]["status"], "pending_review")

    def test_civicsocial_integration_requires_a_configured_valid_bearer_secret(self):
        payload = {"original_post_id": "post-001", "text": "A pothole on the road", "author": "@resident"}
        os.environ.pop("CIVICSOCIAL_INTEGRATION_SECRET", None)
        unavailable = self.client.post("/integrations/civicsocial/complaints", json=payload)
        self.assertEqual(unavailable.status_code, 503)

        os.environ["CIVICSOCIAL_INTEGRATION_SECRET"] = "test-civicsocial-secret"
        missing = self.client.post("/integrations/civicsocial/complaints", json=payload)
        invalid = self.client.post(
            "/integrations/civicsocial/complaints", headers={"Authorization": "Bearer wrong"}, json=payload
        )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(self.db.query(Complaint).filter_by(source="civicsocial").count(), 0)

    def test_civicsocial_submission_is_pending_securely_stored_and_idempotent(self):
        os.environ["CIVICSOCIAL_INTEGRATION_SECRET"] = "test-civicsocial-secret"
        headers = {"Authorization": "Bearer test-civicsocial-secret"}
        image = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
        mp4_bytes = b"\x00\x00\x00\x10ftypisom\x00\x00\x00\x00"
        video = "data:video/mp4;base64," + base64.b64encode(mp4_bytes).decode()
        payload = {
            "original_post_id": "social-12345",
            "text": "A large pothole near the station entrance",
            "author": "@mumbaicitizen",
            "source_post_url": "https://social.example/posts/social-12345",
            "image_base64": image,
            "video_base64": video,
        }
        inference = FakeInference()
        with patch.object(services, "get_infer", return_value=inference), patch.object(
            services, "resolve_location",
            return_value={"lat": None, "lng": None, "display_name": None, "source": "none"},
        ):
            created = self.client.post("/integrations/civicsocial/complaints", headers=headers, json=payload)
            duplicate = self.client.post("/integrations/civicsocial/complaints", headers=headers, json=payload)

        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["status"], "pending_review")
        self.assertEqual(created.json()["review_state"], "pending")
        self.assertTrue(created.json()["created"])
        self.assertFalse(created.json()["already_existed"])
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertFalse(duplicate.json()["created"])
        self.assertTrue(duplicate.json()["already_existed"])
        self.assertEqual(duplicate.json()["complaint_reference"], created.json()["complaint_reference"])

        complaint = self.db.get(Complaint, created.json()["complaint_reference"])
        self.assertEqual(complaint.source, "civicsocial")
        self.assertEqual(complaint.source_post_id, "social-12345")
        self.assertEqual(complaint.source_author, "@mumbaicitizen")
        self.assertEqual(complaint.source_url, payload["source_post_url"])
        self.assertEqual(complaint.review_state, "pending")
        self.assertEqual(complaint.status, "open")
        self.assertEqual(complaint.cluster_id, -1)
        self.assertNotIn(complaint.id, {row["id"] for row in self.client.get("/complaints", headers=self.staff_headers).json()["items"]})
        self.assertEqual(self.client.get("/clusters", headers=self.staff_headers).json(), [])
        review_item = self.client.get("/bmc/reviews", headers=self.staff_headers).json()["items"][0]
        self.assertEqual(review_item["source"], "civicsocial")
        self.assertEqual(review_item["source_author"], "@mumbaicitizen")
        self.assertEqual(review_item["source_url"], payload["source_post_url"])
        self.assertEqual(complaint.uploaded_photo_data, image)
        self.assertTrue(complaint.video_data.startswith("data:video/mp4;base64,"))
        self.assertEqual(len(inference.scored_images), 1)
        self.assertEqual(len(inference.processed_videos), 1)
        self.assertEqual(self.db.query(Complaint).filter_by(source="civicsocial", source_post_id="social-12345").count(), 1)
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(complaint_id=complaint.id, event_key=f"complaint-submitted:{complaint.id}").count(), 1)
        self.assertEqual(self.db.query(Notification).filter_by(complaint_id=complaint.id, audience_role="admin").count(), 1)

    def test_civicsocial_media_validation_rejects_invalid_media(self):
        os.environ["CIVICSOCIAL_INTEGRATION_SECRET"] = "test-civicsocial-secret"
        response = self.client.post(
            "/integrations/civicsocial/complaints",
            headers={"Authorization": "Bearer test-civicsocial-secret"},
            json={"original_post_id": "post-invalid", "text": "A pothole on the road", "image_base64": "data:image/png;base64,YWJj"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.db.query(Complaint).filter_by(source="civicsocial").count(), 0)

    def test_civicsocial_event_failure_returns_retryable_error_and_same_post_recovers_once(self):
        os.environ["CIVICSOCIAL_INTEGRATION_SECRET"] = "test-civicsocial-secret"
        headers = {"Authorization": "Bearer test-civicsocial-secret"}
        payload = {
            "original_post_id": "social-retry-77",
            "text": "A pothole on the road outside the station",
            "author": "@resident",
        }
        second_staff = self._user("second-staff@example.com", "admin")
        inactive_staff = self._user("inactive-staff@example.com", "admin")
        inactive_staff.is_active = False
        self.db.commit()

        write_events = main._record_submission_events
        state = {"failed_attempts": 3}

        def fail_then_write(db, complaint):
            if state["failed_attempts"]:
                state["failed_attempts"] -= 1
                return False
            return write_events(db, complaint)

        with patch.object(services, "get_infer", return_value=FakeInference()), patch.object(
            services, "resolve_location",
            return_value={"lat": None, "lng": None, "display_name": None, "source": "none"},
        ), patch.object(main, "_record_submission_events", side_effect=fail_then_write):
            first = self.client.post("/integrations/civicsocial/complaints", headers=headers, json=payload)
            self.assertEqual(first.status_code, 503, first.text)
            saved = self.db.query(Complaint).filter_by(source="civicsocial", source_post_id="social-retry-77").one()
            self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(complaint_id=saved.id).count(), 0)
            self.assertEqual(self.db.query(Notification).filter_by(complaint_id=saved.id).count(), 0)
            retry = self.client.post("/integrations/civicsocial/complaints", headers=headers, json=payload)

        self.assertEqual(first.status_code, 503, first.text)
        self.assertEqual(first.headers.get("retry-after"), "2")
        self.assertIn(saved.id, first.json()["detail"])
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["complaint_reference"], saved.id)
        self.assertTrue(retry.json()["already_existed"])
        self.assertEqual(self.db.query(Complaint).filter_by(source="civicsocial", source_post_id="social-retry-77").count(), 1)
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(complaint_id=saved.id, event_key=f"complaint-submitted:{saved.id}").count(), 1)
        for staff in (self.staff, second_staff):
            self.assertEqual(self.db.query(Notification).filter_by(complaint_id=saved.id, recipient_user_id=staff.id).count(), 1)
        self.assertEqual(self.db.query(Notification).filter_by(complaint_id=saved.id, recipient_user_id=inactive_staff.id).count(), 0)

    def test_moderation_queue_acceptance_and_rejection_are_authorized_and_cluster_safe(self):
        pending = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-REVIEW001")
        pending.review_state = "pending"; pending.cluster_id = -1
        rejected = self._saved_complaint(citizen_id=self.other.id, complaint_id="CMP-REVIEW002")
        rejected.review_state = "pending"; rejected.cluster_id = -1
        rejected.uploaded_photo_data = "data:image/png;base64,cGhvdG8="
        rejected.video_data = "data:video/mp4;base64,dmlkZW8="
        self.db.commit()
        self.assertEqual(self.client.get("/bmc/reviews").status_code, 401)
        self.assertEqual(self.client.get("/bmc/reviews", headers=self.citizen_headers).status_code, 403)
        self.assertEqual(self.client.post(f"/bmc/reviews/{pending.id}/accept").status_code, 401)
        self.assertEqual(self.client.post(f"/bmc/reviews/{pending.id}/reject", headers=self.citizen_headers, json={}).status_code, 403)
        full_queue = self.client.get("/bmc/reviews", headers=self.staff_headers)
        self.assertEqual({x["id"] for x in full_queue.json()["items"]}, {pending.id, rejected.id})
        first_page = self.client.get("/bmc/reviews?limit=1&offset=0", headers=self.staff_headers)
        self.assertEqual(first_page.status_code, 200, first_page.text)
        self.assertEqual(len(first_page.json()["items"]), 1)
        self.assertEqual(first_page.json()["total"], 2)
        self.assertNotIn(pending.id, {x["id"] for x in self.client.get("/complaints", headers=self.staff_headers).json()["items"]})
        self.assertEqual(self.client.get("/clusters", headers=self.staff_headers).json(), [])
        accepted = self.client.post(f"/bmc/reviews/{pending.id}/accept", headers=self.staff_headers)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["status"], "open")
        self.assertEqual(accepted.json()["cluster_id"], 1)
        self.assertEqual(accepted.json()["rank_in_cluster"], 1)
        self.assertIn(pending.id, {x["id"] for x in self.client.get("/complaints", headers=self.staff_headers).json()["items"]})
        self.assertEqual(self.client.post(f"/bmc/reviews/{pending.id}/accept", headers=self.staff_headers).status_code, 409)
        denied = self.client.post(f"/bmc/reviews/{rejected.id}/reject", headers=self.staff_headers, json={"reason": "Outside service area"})
        self.assertEqual(denied.status_code, 200, denied.text)
        self.assertEqual(denied.json()["status"], "rejected")
        self.assertEqual(self.client.post(f"/bmc/reviews/{rejected.id}/reject", headers=self.staff_headers, json={}).status_code, 409)
        self.db.refresh(rejected)
        self.assertEqual(rejected.cluster_id, -1)
        self.assertEqual(rejected.status, "open")
        self.assertEqual(rejected.review_state, "rejected")
        self.assertEqual(rejected.uploaded_photo_data, "data:image/png;base64,cGhvdG8=")
        self.assertEqual(rejected.video_data, "data:video/mp4;base64,dmlkZW8=")
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(complaint_id=pending.id, event_type="review_accepted").count(), 1)
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(complaint_id=rejected.id, event_type="review_rejected").count(), 1)
        self.assertEqual(self.client.get("/citizen/notifications", headers=self.citizen_headers).json()["unread_count"], 1)
        rejection_notice = self.client.get("/citizen/notifications", headers=self.other_headers).json()["items"][0]
        self.assertIn("Outside service area", rejection_notice["summary"])

    def test_legacy_work_status_is_not_actionable_without_pending_review_state(self):
        accepted_legacy = self._saved_complaint(citizen_id=None, complaint_id="CMP-LEGACY001")
        accepted_legacy.status = "pending_human_review"
        accepted_legacy.review_state = "accepted"
        rejected_legacy = self._saved_complaint(citizen_id=None, complaint_id="CMP-LEGACY002")
        rejected_legacy.status = "pending_human_review"
        rejected_legacy.review_state = "rejected"
        actual_pending = self._saved_complaint(citizen_id=None, complaint_id="CMP-LEGACY003")
        actual_pending.status = "pending_human_review"
        actual_pending.review_state = "pending"
        self.db.commit()

        queue = self.client.get("/bmc/reviews", headers=self.staff_headers)
        self.assertEqual(queue.status_code, 200, queue.text)
        self.assertEqual([row["id"] for row in queue.json()["items"]], [actual_pending.id])
        summary = self.client.get("/bmc/summary", headers=self.staff_headers)
        self.assertEqual(summary.json()["pending_review"], 1)
        self.assertEqual(self.client.post(f"/bmc/reviews/{accepted_legacy.id}/accept", headers=self.staff_headers).status_code, 409)
        detail = self.client.get(f"/bmc/complaints/{actual_pending.id}", headers=self.staff_headers)
        self.assertEqual(detail.json()["source"], actual_pending.source)

    def test_review_decisions_notify_followers_and_owner_once_and_retries_do_not_duplicate(self):
        accepted = self._saved_complaint(citizen_id=self.other.id, complaint_id="CMP-FOLLOWACC1")
        accepted.review_state = "pending"
        accepted.cluster_id = -1
        self.db.add(ComplaintSubscription(complaint_id=accepted.id, user_id=self.citizen.id, active=True))

        rejected = self._saved_complaint(citizen_id=self.other.id, complaint_id="CMP-FOLLOWREJ1")
        rejected.review_state = "pending"
        rejected.cluster_id = -1
        self.db.add(ComplaintSubscription(complaint_id=rejected.id, user_id=self.citizen.id, active=True))

        owner_followed = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-FOLLOWOWN1")
        owner_followed.review_state = "pending"
        owner_followed.cluster_id = -1
        self.db.add(ComplaintSubscription(complaint_id=owner_followed.id, user_id=self.citizen.id, active=True))
        self.db.commit()

        accepted_result = self.client.post(f"/bmc/reviews/{accepted.id}/accept", headers=self.staff_headers)
        self.assertEqual(accepted_result.status_code, 200, accepted_result.text)
        self.assertEqual(self.client.post(f"/bmc/reviews/{accepted.id}/accept", headers=self.staff_headers).status_code, 409)
        self.assertEqual(self.db.query(Notification).filter_by(complaint_id=accepted.id, recipient_user_id=self.citizen.id).count(), 1)
        self.assertEqual(self.db.query(Notification).filter_by(complaint_id=accepted.id, recipient_user_id=self.other.id).count(), 1)
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(complaint_id=accepted.id, event_type="review_accepted").count(), 1)

        rejected_result = self.client.post(f"/bmc/reviews/{rejected.id}/reject", headers=self.staff_headers, json={"reason": "Duplicate report"})
        self.assertEqual(rejected_result.status_code, 200, rejected_result.text)
        self.assertEqual(self.client.post(f"/bmc/reviews/{rejected.id}/reject", headers=self.staff_headers, json={}).status_code, 409)
        self.assertEqual(self.db.query(Notification).filter_by(complaint_id=rejected.id, recipient_user_id=self.citizen.id).count(), 1)
        self.assertEqual(self.db.query(Notification).filter_by(complaint_id=rejected.id, recipient_user_id=self.other.id).count(), 1)
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(complaint_id=rejected.id, event_type="review_rejected").count(), 1)

        owner_result = self.client.post(f"/bmc/reviews/{owner_followed.id}/accept", headers=self.staff_headers)
        self.assertEqual(owner_result.status_code, 200, owner_result.text)
        self.assertEqual(self.client.post(f"/bmc/reviews/{owner_followed.id}/accept", headers=self.staff_headers).status_code, 409)
        self.assertEqual(self.db.query(Notification).filter_by(complaint_id=owner_followed.id, recipient_user_id=self.citizen.id).count(), 1)
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(complaint_id=owner_followed.id, event_type="review_accepted").count(), 1)

    def test_newest_pending_complaint_is_first_even_when_queue_exceeds_one_page(self):
        now = datetime.now(timezone.utc)
        for index in range(26):
            old = self._saved_complaint(citizen_id=None, complaint_id=f"CMP-OLDQ{index:04d}")
            old.review_state = "pending"
            old.cluster_id = -1
            old.created_at = now - timedelta(minutes=26 - index)
        newest = self._saved_complaint(citizen_id=None, complaint_id="CMP-NEWQ0001")
        newest.review_state = "pending"
        newest.cluster_id = -1
        newest.created_at = now
        self.db.commit()
        page = self.client.get("/bmc/reviews?limit=25&offset=0", headers=self.staff_headers)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.json()["total"], 27)
        self.assertEqual(page.json()["items"][0]["id"], newest.id)
        self.assertIn(newest.id, {row["id"] for row in page.json()["items"]})

    def test_pending_external_source_without_owner_still_notifies_staff(self):
        complaint = self._saved_complaint(citizen_id=None, complaint_id="CMP-SOURCE001")
        complaint.source = "twitter"
        complaint.review_state = "pending"
        complaint.cluster_id = -1
        self.db.commit()
        self.assertTrue(main._record_submission_events(self.db, complaint))
        staff_notice = self.db.query(Notification).filter_by(complaint_id=complaint.id, audience_role="admin").one()
        self.assertEqual(staff_notice.title, "New complaint awaiting review")
        self.assertEqual(self.db.query(Notification).filter_by(complaint_id=complaint.id, audience_role="citizen").count(), 0)

    def test_authenticated_tracking_and_follow_subscription_notifications(self):
        complaint = self._saved_complaint(citizen_id=self.other.id, complaint_id="CMP-FOLLOW001")
        complaint.category = "Roads"; complaint.location = "Central Ward"; complaint.embedding = [1.0, 0.0]
        self.db.commit()
        self.assertEqual(self.client.get(f"/citizen/track/{complaint.id}").status_code, 401)
        self.assertEqual(self.client.get(f"/track/{complaint.id}").status_code, 404)
        tracked = self.client.get(f"/citizen/track/{complaint.id}", headers=self.citizen_headers)
        self.assertEqual(tracked.status_code, 200)
        lower_case_tracked = self.client.get(f"/citizen/track/{complaint.id.lower()}", headers=self.citizen_headers)
        self.assertEqual(lower_case_tracked.status_code, 200, lower_case_tracked.text)
        self.assertEqual(lower_case_tracked.json()["reference"], complaint.id)
        tracked_lowercase = self.client.get(f"/citizen/track/{complaint.id.lower()}", headers=self.citizen_headers)
        self.assertEqual(tracked_lowercase.status_code, 200, tracked_lowercase.text)
        self.assertEqual(tracked_lowercase.json()["reference"], complaint.id)
        for forbidden in ("citizen_id", "image_data", "video_data", "transcript", "embedding", "email"):
            self.assertNotIn(forbidden, tracked.json())
        self.assertEqual(tracked.json()["category"], "Roads")
        self.assertEqual(self.client.put(f"/citizen/follows/{complaint.id}", json={"active": True}).status_code, 401)
        follow = self.client.put(f"/citizen/follows/{complaint.id}", headers=self.citizen_headers, json={"active": True})
        self.assertEqual(follow.status_code, 200)
        self.client.put(f"/citizen/follows/{complaint.id}", headers=self.citizen_headers, json={"active": True})
        self.assertEqual(self.db.query(ComplaintSubscription).filter_by(complaint_id=complaint.id, user_id=self.citizen.id).count(), 1)
        self.assertEqual(len(self.client.get("/citizen/follows", headers=self.citizen_headers).json()["items"]), 1)
        status = self.client.patch(f"/complaints/{complaint.id}/status", headers=self.staff_headers, json={"status": "inspection_scheduled"})
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(self.client.get("/citizen/notifications", headers=self.citizen_headers).json()["unread_count"], 1)
        for next_status in ("contractor_allotted", "in_progress", "resolved", "open"):
            status = self.client.patch(f"/complaints/{complaint.id}/status", headers=self.staff_headers, json={"status": next_status})
            self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(self.client.put(f"/citizen/follows/{complaint.id}", headers=self.citizen_headers, json={"active": False}).status_code, 200)
        self.assertEqual(self.client.get("/citizen/follows", headers=self.citizen_headers).json()["items"], [])

    def test_submission_event_write_failure_is_reported_and_retry_is_deduplicated(self):
        complaint = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-EVENTFAIL1")
        with patch.object(self.db, "commit", side_effect=RuntimeError("simulated database failure")):
            recorded = main._record_submission_events(self.db, complaint)
        self.assertFalse(recorded)
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(event_key=f"complaint-submitted:{complaint.id}").count(), 0)
        self.assertEqual(self.db.query(Notification).filter_by(event_key=f"complaint-received:{complaint.id}").count(), 0)

        self.assertTrue(main._record_submission_events(self.db, complaint))
        self.assertTrue(main._record_submission_events(self.db, complaint))
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(event_key=f"complaint-submitted:{complaint.id}").count(), 1)
        self.assertEqual(self.db.query(Notification).filter_by(event_key=f"complaint-received:{complaint.id}").count(), 1)

    def test_submission_event_retry_recovers_transient_commit_failure(self):
        complaint = self._saved_complaint(citizen_id=self.citizen.id, complaint_id="CMP-EVENTRETRY1")
        real_commit = self.db.commit
        fail_once = {"pending": True}

        def commit_with_one_transient_failure():
            if fail_once["pending"]:
                fail_once["pending"] = False
                raise RuntimeError("simulated transient database failure")
            return real_commit()

        with patch.object(self.db, "commit", side_effect=commit_with_one_transient_failure) as commit:
            self.assertTrue(main._record_submission_events_with_retry(self.db, complaint))
        self.assertEqual(commit.call_count, 2)
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(event_key=f"complaint-submitted:{complaint.id}").count(), 1)
        self.assertEqual(self.db.query(Notification).filter_by(event_key=f"complaint-received:{complaint.id}").count(), 1)

    def test_event_persistence_exhaustion_keeps_citizen_submission_successful(self):
        with patch.object(services, "get_infer", return_value=FakeInference()), patch.object(
            services, "resolve_location", return_value={"lat": None, "lng": None, "display_name": None, "source": "none"}
        ), patch.object(main, "_record_submission_events", return_value=False) as record_events:
            response = self.client.post(
                "/citizen/complaints", headers=self.citizen_headers,
                json={"text": "A pothole by my street"},
            )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(record_events.call_count, 3)
        self.assertEqual(self.db.query(Complaint).filter_by(id=response.json()["id"]).count(), 1)
        self.assertEqual(self.db.query(ComplaintStatusHistory).filter_by(complaint_id=response.json()["id"]).count(), 0)


if __name__ == "__main__":
    unittest.main()
