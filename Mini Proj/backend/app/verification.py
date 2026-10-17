"""Layer 1 resolution verification using the validated ORB + RANSAC flow."""

import base64

import cv2
import numpy as np


def _decode_gray(image_b64):
    raw = base64.b64decode(image_b64.split(",", 1)[-1])
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("The supplied data is not a decodable image")
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def location_match_score(before_gray, after_gray, ratio_thresh=0.75):
    orb = cv2.ORB_create(nfeatures=2000)
    keypoints_before, descriptors_before = orb.detectAndCompute(before_gray, None)
    keypoints_after, descriptors_after = orb.detectAndCompute(after_gray, None)
    if (
        descriptors_before is None
        or descriptors_after is None
        or len(keypoints_before) < 8
        or len(keypoints_after) < 8
    ):
        return 0.0, 0, None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw_matches = matcher.knnMatch(descriptors_before, descriptors_after, k=2)
    good_matches = []
    for pair in raw_matches:
        if len(pair) == 2 and pair[0].distance < ratio_thresh * pair[1].distance:
            good_matches.append(pair[0])
    if len(good_matches) < 8:
        return 0.0, len(good_matches), None

    source_points = np.float32(
        [keypoints_before[match.queryIdx].pt for match in good_matches]
    ).reshape(-1, 1, 2)
    destination_points = np.float32(
        [keypoints_after[match.trainIdx].pt for match in good_matches]
    ).reshape(-1, 1, 2)
    homography, mask = cv2.findHomography(
        source_points,
        destination_points,
        cv2.RANSAC,
        5.0,
    )
    if homography is None or mask is None:
        return 0.0, len(good_matches), None

    inliers = int(mask.sum())
    score = inliers / max(len(good_matches), 1)
    if inliers < 15:
        score *= inliers / 15
    return round(score, 3), inliers, homography


def issue_region_change_score(
    before_gray,
    after_gray,
    homography,
    roi_center=(320, 260),
    roi_radius=70,
):
    height, width = before_gray.shape
    warped_after = cv2.warpPerspective(after_gray, homography, (width, height))
    roi_mask = np.zeros((height, width), dtype=np.uint8)
    cv2.circle(roi_mask, roi_center, roi_radius, 255, -1)
    valid_mask = (warped_after > 0).astype(np.uint8) * 255
    roi_mask = cv2.bitwise_and(roi_mask, valid_mask)
    if roi_mask.sum() == 0:
        return 0.0

    difference = cv2.absdiff(before_gray, warped_after)
    return round(float(difference[roi_mask > 0].mean()), 2)


def verify_resolution(original_b64, resolved_b64):
    try:
        before_gray = _decode_gray(original_b64)
        after_gray = _decode_gray(resolved_b64)
    except (ValueError, TypeError, base64.binascii.Error):
        return 0.0, 0.0, "Unable to decode one or both verification images"

    location_score, _, homography = location_match_score(before_gray, after_gray)
    if location_score < 0.35:
        return location_score, 0.0, "Location mismatch - cannot verify same site"

    change_score = issue_region_change_score(before_gray, after_gray, homography)
    if change_score < 15:
        return (
            location_score,
            change_score,
            "Same location, no significant change - not resolved",
        )
    return (
        location_score,
        change_score,
        "Likely resolved - same location, significant change detected",
    )
