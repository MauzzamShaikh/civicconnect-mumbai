import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  CircleMarker,
  MapContainer,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";
import "./style.css";

const api = "/api";
const tone = { Low: "#22a06b", Medium: "#e7a221", High: "#dc4141" };
const nextStaffStatuses = {
  open: ["inspection_scheduled", "contractor_allotted", "in_progress", "resolved"],
  inspection_scheduled: ["open", "contractor_allotted", "in_progress", "resolved"],
  contractor_allotted: ["inspection_scheduled", "in_progress", "resolved"],
  in_progress: ["inspection_scheduled", "contractor_allotted", "resolved"],
  resolved: ["open"],
};

function CivicMark() {
  return (
    <div className="civic-mark" aria-hidden="true">
      <svg viewBox="0 0 48 48" width="34" height="34" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" focusable="false">
        <path d="M7 18 24 7l17 11H7Z" />
        <path d="M10 21v15m7-15v15m7-15v15m7-15v15m7-15v15M7 39h34M5 42h38" />
      </svg>
    </div>
  );
}

function MapResizeObserver() {
  const map = useMap();
  useEffect(() => {
    const container = map.getContainer();
    const observer = new ResizeObserver(() => map.invalidateSize({ pan: false, debounceMoveend: true }));
    observer.observe(container);
    return () => observer.disconnect();
  }, [map]);
  return null;
}

function App() {
  const [clusters, setClusters] = useState([]);
  const [selected, setSelected] = useState(null);
  const [clusterDetail, setClusterDetail] = useState(null);
  const [text, setText] = useState("");
  const [photo, setPhoto] = useState(null);
  const [video, setVideo] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [staffToken, setStaffToken] = useState("");
  const [authStatus, setAuthStatus] = useState(() => sessionStorage.getItem("bmcStaffToken") ? "checking" : "signed-out");
  const [staffEmail, setStaffEmail] = useState("");
  const [staffPassword, setStaffPassword] = useState("");
  const [staffBusy, setStaffBusy] = useState(false);
  const [staffError, setStaffError] = useState("");
  const [evidenceByComplaint, setEvidenceByComplaint] = useState({});
  const [register, setRegister] = useState({ items: [], total: 0, categories: [] });
  const [registerFilters, setRegisterFilters] = useState({ q: "", status: "", category: "", severity: "" });
  const [registerLoading, setRegisterLoading] = useState(false);
  const [statusBusy, setStatusBusy] = useState({});
  const [registerError, setRegisterError] = useState("");
  const [noticeData, setNoticeData] = useState({ items: [], unread_count: 0 });
  const [noticeLoading, setNoticeLoading] = useState(false);
  const [noticeError, setNoticeError] = useState("");
  const [pageView, setPageView] = useState(window.location.pathname === "/notifications" ? "notifications" : "overview");
  const [notificationPreviewOpen, setNotificationPreviewOpen] = useState(false);
  const [reviewItems, setReviewItems] = useState([]);
  const [focusedReview, setFocusedReview] = useState(null);
  const [reviewTotal, setReviewTotal] = useState(0);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState("");
  const [reviewBusy, setReviewBusy] = useState({});
  const [reviewMedia, setReviewMedia] = useState({});
  const [evidenceBusyCount, setEvidenceBusyCount] = useState(0);
  const [overviewStats, setOverviewStats] = useState({});
  const photoInputRef = useRef(null);
  const videoInputRef = useRef(null);
  const reviewRequestRef = useRef(false);
  const notificationRequestRef = useRef(false);
  const reviewRefreshRequestedRef = useRef(false);
  const summaryRequestRef = useRef(false);
  const reviewLoadedCountRef = useRef(0);
  const focusedReviewRef = useRef(null);
  const evidenceBusyRef = useRef(0);
  const notificationRegionRef = useRef(null);
  const notificationButtonRef = useRef(null);
  const notificationEscapeRef = useRef(false);

  const clearStaffSession = (notice = "") => {
    sessionStorage.removeItem("bmcStaffToken");
    setStaffToken("");
    setAuthStatus("signed-out");
    setStaffPassword("");
    setClusters([]);
    setSelected(null);
    setClusterDetail(null);
    setEvidenceByComplaint({});
    setRegister({ items: [], total: 0, categories: [] });
    setStatusBusy({});
    setNotificationPreviewOpen(false);
    setNoticeData({ items: [], unread_count: 0 });
    setMessage(null);
    setText("");
    setPhoto(null);
    setVideo(null);
    if (photoInputRef.current) photoInputRef.current.value = "";
    if (videoInputRef.current) videoInputRef.current.value = "";
    setStaffError(notice);
  };

  const staffFetch = async (path, options = {}) => {
    const token = sessionStorage.getItem("bmcStaffToken") || staffToken;
    if (!token) throw new Error("Sign in with an authorized BMC staff account.");
    const response = await fetch(`${api}${path}`, {
      ...options,
      headers: { ...(options.headers || {}), Authorization: `Bearer ${token}` },
    });
    if (response.status === 401) {
      clearStaffSession("Your staff session expired. Please sign in again.");
      const error = new Error("Your staff session expired. Please sign in again.");
      error.status = 401;
      throw error;
    }
    if (response.status === 403) {
      clearStaffSession("This account is not authorized to use the BMC staff portal.");
      const error = new Error("This account is not authorized to use the BMC staff portal.");
      error.status = 403;
      throw error;
    }
    return response;
  };

  const reload = async () => {
    const response = await staffFetch("/clusters");
    if (!response.ok) throw new Error("Could not refresh the map.");
    const nextClusters = await response.json();
    setClusters(nextClusters);
    return nextClusters;
  };
  const loadOverviewStats = async () => {
    if (summaryRequestRef.current) return;
    summaryRequestRef.current = true;
    try { const response = await staffFetch("/bmc/summary"); if (response.ok) setOverviewStats(await response.json()); }
    catch { /* session helper reports auth failures */ }
    finally { summaryRequestRef.current = false; }
  };

  const loadReviews = async (offset = 0, append = false) => {
    if (reviewRequestRef.current) return;
    reviewRequestRef.current = true;
    setReviewLoading(true);
    try {
      const response = await staffFetch(`/bmc/reviews?limit=25&offset=${offset}`);
      if (!response.ok) throw new Error("Could not load complaints awaiting review.");
      const data = await response.json();
      const next = append ? [...reviewItems.filter(existing => !(data.items || []).some(item => item.id === existing.id)), ...(data.items || [])] : (data.items || []);
      setReviewItems(next);
      reviewLoadedCountRef.current = next.length;
      const visible = new Set(next.map(item => item.id));
      setReviewMedia(media => Object.fromEntries(Object.entries(media).filter(([id]) => visible.has(id))));
      setReviewTotal(data.total || 0);
      setReviewError("");
    } catch (error) { setReviewError(error.message); }
    finally {
      reviewRequestRef.current = false; setReviewLoading(false);
      if (reviewRefreshRequestedRef.current) { reviewRefreshRequestedRef.current = false; window.setTimeout(refreshReviewQueue, 0); }
    }
  };

  const refreshReviewQueue = async () => {
    if (reviewRequestRef.current) { reviewRefreshRequestedRef.current = true; return; }
    const targetCount = Math.max(25, reviewLoadedCountRef.current);
    reviewRequestRef.current = true;
    setReviewLoading(true);
    try {
      const all = [];
      let total = 0;
      for (let offset = 0; offset < targetCount; offset += 25) {
        const response = await staffFetch(`/bmc/reviews?limit=25&offset=${offset}`);
        if (!response.ok) throw new Error("Could not load complaints awaiting review.");
        const data = await response.json();
        all.push(...(data.items || [])); total = data.total || 0;
        if (!(data.items || []).length || all.length >= total) break;
      }
      setReviewItems(all);
      reviewLoadedCountRef.current = all.length;
      setReviewTotal(total);
      const visible = new Set(all.map(item => item.id));
      setReviewMedia(media => Object.fromEntries(Object.entries(media).filter(([id]) => visible.has(id))));
      const focused = focusedReviewRef.current;
      if (focused) {
        const response = await staffFetch(`/bmc/complaints/${encodeURIComponent(focused.id)}`);
        if (response.ok) {
          const latest = await response.json();
          if (latest.status !== "pending_review") { focusedReviewRef.current = null; setFocusedReview(null); }
          else { focusedReviewRef.current = latest; setFocusedReview(latest); }
        }
      }
      setReviewError("");
    } catch (error) { setReviewError(error.message); }
    finally {
      reviewRequestRef.current = false; setReviewLoading(false);
      if (reviewRefreshRequestedRef.current) { reviewRefreshRequestedRef.current = false; window.setTimeout(refreshReviewQueue, 0); }
    }
  };

  const loadReviewEvidence = async (complaintId) => {
    if (evidenceBusyRef.current >= 2 || reviewMedia[complaintId]?.loading) return;
    evidenceBusyRef.current += 1;
    setEvidenceBusyCount(evidenceBusyRef.current);
    setReviewMedia(current => ({ ...current, [complaintId]: { ...(current[complaintId] || {}), loading: true, error: "" } }));
    try {
      const response = await staffFetch(`/bmc/complaints/${encodeURIComponent(complaintId)}/media`);
      if (!response.ok) throw new Error("Evidence could not be loaded. Retry to try again.");
      const data = await response.json();
      setReviewMedia(current => ({ ...current, [complaintId]: { ...data, loaded: true, loading: false } }));
    } catch (error) {
      setReviewMedia(current => ({ ...current, [complaintId]: { ...(current[complaintId] || {}), loading: false, error: error.message } }));
    } finally { evidenceBusyRef.current -= 1; setEvidenceBusyCount(evidenceBusyRef.current); }
  };

  const decideReview = async (item, decision) => {
    const reason = decision === "reject" ? window.prompt("Optional reason for rejection (up to 500 characters):") : null;
    if (decision === "reject" && reason === null) return;
    setReviewBusy(current => ({ ...current, [item.id]: true }));
    try {
      const response = await staffFetch(`/bmc/reviews/${encodeURIComponent(item.id)}/${decision}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(decision === "reject" ? { reason } : {}),
      });
      const result = await response.json().catch(() => ({}));
      if (response.status === 409) { await refreshReviewQueue(); setReviewError("Another staff member has already reviewed this complaint. The queue has been refreshed."); return; }
      if (!response.ok) throw new Error(result.detail || "The review decision could not be saved.");
      setMessage({ type: "success", text: `${item.id} ${decision === "accept" ? "accepted and added to operations" : "rejected"}.` });
      await refreshReviewQueue(); await loadRegister(); await reload(); refreshNotifications(); loadOverviewStats();
    } catch (error) { setReviewError(error.message); }
    finally { setReviewBusy(current => ({ ...current, [item.id]: false })); }
  };

  const changePage = (next) => { window.history.pushState({}, "", next === "notifications" ? "/notifications" : "/"); setPageView(next); setNotificationPreviewOpen(false); if (next === "notifications") refreshNotifications(); else window.setTimeout(() => document.getElementById(next === "register" || next === "map" ? "complaint-map" : "top")?.scrollIntoView({ behavior: "smooth" }), 0); };

  useEffect(() => {
    let active = true;
    const token = sessionStorage.getItem("bmcStaffToken");
    if (!token) {
      setAuthStatus("signed-out");
      return () => { active = false; };
    }
    fetch(`${api}/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then(async (response) => ({ response, user: response.ok ? await response.json() : null }))
      .then(({ response, user }) => {
        if (!active) return;
        if (response.status === 401) {
          clearStaffSession("Your staff session expired. Please sign in again.");
        } else if (!response.ok || user?.role !== "admin") {
          clearStaffSession("This account is not authorized to use the BMC staff portal.");
        } else {
          setStaffToken(token);
          setAuthStatus("authorized");
          setStaffError("");
        }
      })
      .catch(() => {
        if (active) clearStaffSession("Could not validate your staff session. Please sign in again.");
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const onPopState = () => { setPageView(window.location.pathname === "/notifications" ? "notifications" : "overview"); setNotificationPreviewOpen(false); };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (authStatus !== "authorized") return;
    reload().catch((error) => {
      if (error.status !== 401 && error.status !== 403) {
        setMessage({ type: "error", text: "Could not load the complaint map. Please retry." });
      }
    });
  }, [authStatus]);

  const loadRegister = async (filters = registerFilters, offset = 0, append = false) => {
    setRegisterLoading(true); setRegisterError("");
    try {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
      params.set("limit", "50"); params.set("offset", String(offset));
      const response = await staffFetch(`/complaints?${params}`);
      if (!response.ok) throw new Error("Could not load the complaint register.");
      const data = await response.json();
      setRegister((current) => append ? { ...data, items: [...current.items, ...(data.items || [])] } : data); return data;
    } catch (error) {
      if (error.status !== 401 && error.status !== 403) setRegisterError(error.message || "Could not load complaints.");
      return null;
    } finally { setRegisterLoading(false); }
  };

  const refreshNotifications = async () => {
    if (notificationRequestRef.current) return;
    notificationRequestRef.current = true;
    setNoticeLoading(true); setNoticeError("");
    try {
      const response = await staffFetch("/bmc/notifications");
      if (!response.ok) throw new Error("Staff notifications are temporarily unavailable.");
      const data = await response.json();
      if (!data || !Array.isArray(data.items) || !Number.isInteger(data.unread_count)) throw new Error("Staff notifications returned an invalid response.");
      setNoticeData(data);
    } catch (error) { if (error.status !== 401 && error.status !== 403) setNoticeError("Staff notifications are temporarily unavailable. Retry to refresh."); }
    finally { notificationRequestRef.current = false; setNoticeLoading(false); }
  };

  useEffect(() => {
    if (authStatus !== "authorized") return;
    loadRegister(); refreshNotifications(); loadOverviewStats();
    const timer = window.setInterval(refreshNotifications, 60000);
    return () => window.clearInterval(timer);
  }, [authStatus]);

  useEffect(() => {
    if (authStatus === "authorized" && pageView === "notifications") loadReviews();
  }, [authStatus, pageView]);

  useEffect(() => {
    if (authStatus !== "authorized" || pageView !== "notifications") return;
    const timer = window.setInterval(() => { refreshReviewQueue(); loadOverviewStats(); refreshNotifications(); }, 30000);
    return () => window.clearInterval(timer);
  }, [authStatus, pageView]);

  useEffect(() => {
    if (authStatus !== "authorized") return;
    const timer = window.setTimeout(() => loadRegister(registerFilters), 250);
    return () => window.clearTimeout(timer);
  }, [registerFilters.q, registerFilters.status, registerFilters.category, registerFilters.severity]);

  const markNotice = async (notice) => {
    try {
      const response = await staffFetch(`/bmc/notifications/${notice.id}/read`, { method: "PATCH" });
      if (!response.ok) throw new Error("The notification could not be marked as read. Please retry.");
      const data = await response.json();
      if (!data || !Array.isArray(data.items) || !Number.isInteger(data.unread_count)) throw new Error("The updated notification state could not be verified. Refresh notifications and retry.");
      setNoticeData(data);
      if (notice.complaint_id) {
        let row = register.items.find((item) => item.id === notice.complaint_id);
        if (!row) {
          const detailResponse = await staffFetch(`/bmc/complaints/${encodeURIComponent(notice.complaint_id)}`);
          if (detailResponse.ok) row = await detailResponse.json();
        }
        if (row?.status === "pending_review") {
          focusedReviewRef.current = row;
          setFocusedReview(row);
          setNotificationPreviewOpen(false);
          changePage("notifications");
          await refreshReviewQueue();
          window.setTimeout(() => document.getElementById(`review-${row.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 80);
          return;
        }
        if (row?.status === "rejected") {
          setNotificationPreviewOpen(false);
          changePage("notifications");
          await refreshReviewQueue();
          setReviewError("This complaint has been rejected and is no longer in the active map.");
          return;
        }
        if (row) { changePage("map"); await openComplaint(row); }
      }
      setNotificationPreviewOpen(false);
    } catch (error) {
      if (error.status !== 401 && error.status !== 403) setNoticeError(error.message || "The notification could not be updated.");
    }
  };

  const setNoticeRead = async (notice) => {
    try {
      const response = await staffFetch(`/bmc/notifications/${notice.id}/read`, { method: "PATCH" });
      if (!response.ok) throw new Error("The notification could not be marked as read. Please retry.");
      const data = await response.json();
      if (!data || !Array.isArray(data.items) || !Number.isInteger(data.unread_count)) throw new Error("The updated notification state could not be verified. Refresh notifications and retry.");
      setNoticeData(data);
      setNoticeError("");
    } catch (error) {
      if (error.status !== 401 && error.status !== 403) setNoticeError(error.message || "The notification could not be updated.");
    }
  };

  const setAllNoticesRead = async () => {
    try {
      const response = await staffFetch("/bmc/notifications/read-all", { method: "POST" });
      if (!response.ok) throw new Error("Notifications could not be marked as read. Please retry.");
      const data = await response.json();
      if (!data || !Array.isArray(data.items) || !Number.isInteger(data.unread_count)) throw new Error("The updated notification state could not be verified. Refresh notifications and retry.");
      setNoticeData(data);
      setNoticeError("");
    } catch (error) {
      if (error.status !== 401 && error.status !== 403) setNoticeError(error.message || "Notifications could not be updated.");
    }
  };

  const openComplaint = async (complaint) => {
    if (complaint.status === "pending_review" || complaint.status === "rejected" || complaint.cluster_id < 0) {
      setMessage({ type: "error", text: complaint.status === "rejected" ? "This complaint is rejected and is not on the active map." : "This complaint is not attached to an active cluster." });
      return;
    }
    let target = clusters.find((item) => item.cluster_id === complaint.cluster_id);
    if (!target) {
      const refreshed = await reload();
      target = refreshed.find((item) => item.cluster_id === complaint.cluster_id);
    }
    if (target) await openCluster(target);
    else {
      setMessage({ type: "error", text: "The complaint is not currently attached to a map cluster." });
      document.getElementById("complaint-map")?.scrollIntoView({ behavior: "smooth" });
    }
  };

  const updateStatus = async (complaint, nextStatus) => {
    setStatusBusy((current) => ({ ...current, [complaint.id]: true }));
    setMessage({ type: "info", text: "Updating complaint status…" });
    try {
      const response = await staffFetch(`/complaints/${encodeURIComponent(complaint.id)}/status`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: nextStatus }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail || "Could not update complaint status.");
      await loadRegister();
      const nextClusters = await reload();
      if (selected?.cluster_id === complaint.cluster_id) {
        if (nextClusters.some((cluster) => cluster.cluster_id === complaint.cluster_id)) await loadClusterDetails(complaint.cluster_id);
        else { setSelected(null); setClusterDetail(null); }
      }
      setMessage({ type: "success", text: `Complaint ${complaint.id} is now ${nextStatus.replaceAll("_", " ")}.` });
      refreshNotifications(); loadOverviewStats();
    } catch (error) {
      if (error.status !== 401 && error.status !== 403) setMessage({ type: "error", text: error.message });
    } finally {
      setStatusBusy((current) => ({ ...current, [complaint.id]: false }));
    }
  };

  const loadClusterDetails = async (clusterId) => {
    const response = await staffFetch(`/clusters/${clusterId}`);
    if (!response.ok) throw new Error("Could not load the cluster reports.");
    const details = await response.json();
    if (!Array.isArray(details)) throw new Error("The cluster response was invalid.");
    setClusterDetail(details);
    return details;
  };

  const signInStaff = async (event) => {
    event.preventDefault();
    setStaffBusy(true);
    setStaffError("");
    try {
      const response = await fetch(`${api}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: staffEmail, password: staffPassword }),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok || !result?.access_token) {
        throw new Error("Sign-in failed. Check your staff email and password.");
      }
      const validation = await fetch(`${api}/auth/me`, {
        headers: { Authorization: `Bearer ${result.access_token}` },
      });
      const user = validation.ok ? await validation.json() : null;
      if (!validation.ok || user?.role !== "admin") {
        throw new Error("This account is not authorized for the BMC staff portal.");
      }
      sessionStorage.setItem("bmcStaffToken", result.access_token);
      setStaffToken(result.access_token);
      setAuthStatus("authorized");
      setStaffPassword("");
      setStaffError("");
    } catch (error) {
      setStaffError(error instanceof Error ? error.message : "Could not sign in.");
    } finally {
      setStaffBusy(false);
    }
  };

  const signOutStaff = () => {
    clearStaffSession();
  };

  const showNotificationPreview = () => {
    if (notificationEscapeRef.current) {
      notificationEscapeRef.current = false;
      return;
    }
    if (!notificationPreviewOpen) {
      setNotificationPreviewOpen(true);
      refreshNotifications();
    }
  };

  const staffHeader = (
    <header className="bmc-dashboard-header">
      <div className="bmc-header-brand">
        <CivicMark />
        <div className="bmc-header-brand-copy">
          <h1>BMC Civic Complaints</h1>
        </div>
      </div>
      <nav className="bmc-header-nav" aria-label="Staff navigation">
        <button type="button" className={pageView === "overview" || pageView === "map" ? "active" : ""} aria-current={pageView === "overview" || pageView === "map" ? "page" : undefined} onClick={() => changePage("overview")}>Dashboard Overview</button>
        <button type="button" className={pageView === "register" ? "active" : ""} aria-current={pageView === "register" ? "page" : undefined} onClick={() => changePage("register")}>Complaint Register</button>
      </nav>
      <div className="bmc-header-actions">
        <div className="staff-notifications" ref={notificationRegionRef}
          onMouseEnter={showNotificationPreview}
          onMouseLeave={() => setNotificationPreviewOpen(false)}
          onFocusCapture={showNotificationPreview}
          onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setNotificationPreviewOpen(false); }}
          onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); setNotificationPreviewOpen(false); if (document.activeElement !== notificationButtonRef.current) { notificationEscapeRef.current = true; notificationButtonRef.current?.focus(); } } }}>
          <button ref={notificationButtonRef} className={`bmc-header-notifications ${pageView === "notifications" ? "active" : ""}`} type="button" aria-current={pageView === "notifications" ? "page" : undefined} aria-label={`Open notifications${noticeData.unread_count > 0 ? `, ${noticeData.unread_count} unread` : ""}`} aria-expanded={notificationPreviewOpen} aria-haspopup="true" onClick={() => { setNotificationPreviewOpen(false); changePage("notifications"); }}>
            <svg aria-hidden="true" viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></svg>
            <span>Notifications</span>
            {noticeData.unread_count > 0 && <span className="bmc-header-unread-count">{noticeData.unread_count}</span>}
          </button>
          {notificationPreviewOpen && <div className="staff-notice-popover" role="region" aria-label="Notification preview">
            <div className="staff-notice-heading"><strong>Recent activity</strong><button type="button" className="bmc-preview-refresh" disabled={noticeLoading} onClick={refreshNotifications}>{noticeLoading ? "Refreshing…" : "Refresh"}</button></div>
            {noticeError && <p className="error" role="alert">{noticeError}</p>}
            {noticeData.items.length ? noticeData.items.slice(0, 5).map((notice) => <button className={`staff-notice-item ${notice.read ? "" : "unread"}`} key={notice.id} onClick={() => markNotice(notice)}><b>{!notice.read && <span className="bmc-unread-dot" aria-label="Unread" />} {notice.title}</b><span>{notice.summary}</span><small>{notice.created_at ? new Date(notice.created_at).toLocaleString() : ""}</small></button>) : <p>{noticeLoading ? "Loading notifications…" : "No notifications yet."}</p>}
            <div className="bmc-header-notice-footer"><span>{overviewStats.pending_review || 0} pending review</span><a href="/notifications" onClick={(event) => { event.preventDefault(); changePage("notifications"); }}>View all notifications</a></div>
          </div>}
        </div>
        <div className="bmc-header-identity" title={staffEmail || "Authorized BMC staff"}>
          <span className="bmc-header-identity-label">Signed in</span>
          <span className="bmc-header-identity-value">{staffEmail || "BMC staff"}</span>
        </div>
        <button className="bmc-header-signout" type="button" onClick={signOutStaff}>
          <svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M10 17l5-5-5-5M15 12H3"/><path d="M12 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6"/></svg>
          <span>Sign out</span>
        </button>
      </div>
    </header>
  );

  const loadEvidence = async (complaintId) => {
    if (!staffToken || authStatus !== "authorized") {
      clearStaffSession("Your staff session is missing or no longer authorized. Please sign in again.");
      return;
    }
    setEvidenceByComplaint((current) => ({ ...current, [complaintId]: { loading: true } }));
    try {
      const response = await staffFetch(`/bmc/complaints/${encodeURIComponent(complaintId)}/media`);
      if (!response.ok) throw new Error("Could not load complaint evidence.");
      const evidence = await response.json();
      setEvidenceByComplaint((current) => ({ ...current, [complaintId]: evidence }));
    } catch (error) {
      if (error.status !== 401 && error.status !== 403) {
        setEvidenceByComplaint((current) => ({ ...current, [complaintId]: { error: error.message } }));
      }
    }
  };

  const openCluster = async (cluster) => {
    setSelected(cluster);
    setClusterDetail(null);

    try {
      await loadClusterDetails(cluster.cluster_id);
    } catch (error) {
      if (error.status !== 401 && error.status !== 403) setClusterDetail([]);
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!text.trim() && !photo && !video) {
      setMessage({ type: "error", text: "Add a description, photo, or video before submitting." });
      return;
    }

    setBusy(true);
    setMessage(null);
    const toBase64 = (file) => new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("Could not read the selected file."));
      reader.onerror = () => reject(new Error("Could not read the selected file."));
      reader.onabort = () => reject(new Error("Reading the selected file was cancelled."));
      reader.readAsDataURL(file);
    });

    try {
      const image_base64 = photo ? await toBase64(photo) : null;
      const video_base64 = video ? await toBase64(video) : null;
      const response = await staffFetch("/complaints", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, image_base64, video_base64 }),
      });
      let complaint;
      try {
        complaint = await response.json();
      } catch {
        throw new Error("The server returned an invalid response. Please try again.");
      }
      if (!response.ok) throw new Error(complaint?.detail || "Submission failed. Please try again.");
      if (!complaint?.id || complaint.cluster_id == null) throw new Error("The submission response was incomplete. Please try again.");

      setMessage({ type: "success", text: `Submitted ${complaint.id}: ${complaint.severity}, cluster ${complaint.cluster_id}` });
      setText("");
      setPhoto(null);
      setVideo(null);
      if (photoInputRef.current) photoInputRef.current.value = "";
      if (videoInputRef.current) videoInputRef.current.value = "";

      const nextClusters = await reload();
      await Promise.all([loadRegister(registerFilters), loadOverviewStats()]);
      if (selected?.cluster_id === complaint.cluster_id) {
        const refreshedSelected = nextClusters.find((cluster) => cluster.cluster_id === complaint.cluster_id);
        if (refreshedSelected) setSelected(refreshedSelected);
        await loadClusterDetails(complaint.cluster_id);
      }
    } catch (error) {
      setMessage({ type: "error", text: error instanceof Error ? error.message : "Submission failed. Please try again." });
    } finally {
      setBusy(false);
    }
  };

  if (authStatus === "checking") {
    return <main className="staff-auth-page"><section className="staff-auth-card" aria-live="polite"><CivicMark /><p className="staff-eyebrow">BMC Civic Intelligence</p><h1>Verifying staff session</h1><p className="staff-auth-copy">Please wait while we confirm your authorized access.</p><div className="auth-spinner" role="status">Checking secure session…</div></section></main>;
  }

  if (authStatus !== "authorized") {
    return (
      <main className="staff-auth-page">
        <section className="staff-auth-card">
          <CivicMark />
          <p className="staff-eyebrow">BMC Civic Intelligence</p>
          <h1>Welcome back</h1>
          <p className="staff-auth-copy">Sign in to the restricted operations portal for authorized Brihanmumbai Municipal Corporation personnel.</p>
          <form className="staff-login-form" onSubmit={signInStaff}>
            <label htmlFor="staff-email">Staff email</label>
            <input id="staff-email" type="email" autoComplete="username" required value={staffEmail} onChange={(event) => setStaffEmail(event.target.value)} />
            <label htmlFor="staff-password">Password</label>
            <input id="staff-password" type="password" autoComplete="current-password" required value={staffPassword} onChange={(event) => setStaffPassword(event.target.value)} />
            {staffError && <p className="staff-auth-error" role="alert">{staffError}</p>}
            <button className="staff-signin-button" type="submit" disabled={staffBusy}>{staffBusy ? "Verifying access…" : "Sign in securely"}</button>
          </form>
          <p className="staff-provisioning-note">Staff accounts are provisioned by an authorized administrator. Public registration is not available on this portal.</p>
        </section>
      </main>
    );
  }

  if (pageView === "notifications") return (
    <main className="bmc-portal">
      {staffHeader}
      <div className="bmc-page-content">
        <section className="bmc-page-heading">
          <div><p className="bmc-eyebrow">CIVIC OPERATIONS</p><h2>Notifications</h2><p>Recent complaint activity and reports awaiting review.</p></div>
          <button className="bmc-secondary-button" type="button" onClick={refreshReviewQueue} disabled={reviewLoading}>{reviewLoading ? "Refreshing…" : "Refresh queue"}</button>
        </section>
        <section className="review-section bmc-review-section" aria-labelledby="notification-history-title">
          <div className="staff-register-heading"><div><h2 id="notification-history-title">Recent activity</h2><p>{noticeData.unread_count} unread notification{noticeData.unread_count === 1 ? "" : "s"}</p></div><div className="bmc-notification-actions"><button className="bmc-secondary-button" type="button" onClick={refreshNotifications} disabled={noticeLoading}>{noticeLoading ? "Refreshing…" : "Refresh notifications"}</button><button className="bmc-secondary-button" type="button" onClick={setAllNoticesRead} disabled={!noticeData.unread_count}>Mark all as read</button></div></div>
          {noticeError && <p className="error" role="alert">{noticeError}</p>}
          {noticeData.items.length ? noticeData.items.map((notice) => <article className={`bmc-notification-history-item ${notice.read ? "" : "unread"}`} key={notice.id}>
            <div><strong>{!notice.read && <span className="bmc-unread-dot" aria-label="Unread" />} {notice.title}</strong><p>{notice.summary}</p><small>{notice.created_at ? new Date(notice.created_at).toLocaleString() : ""}</small></div>
            {!notice.read ? <button className="bmc-secondary-button" type="button" onClick={() => setNoticeRead(notice)}>Mark as read</button> : <span className="bmc-notification-read-state">Read</span>}
          </article>) : <p className="staff-register-empty">{noticeLoading ? "Loading notifications…" : "No notifications yet."}</p>}
        </section>
        <section className="review-section bmc-review-section" aria-labelledby="review-queue-title">
          <div className="staff-register-heading"><div><h2 id="review-queue-title">New complaints awaiting review</h2><p>Pending queue is based on complaint review state.</p></div><span className="bmc-count-badge">{reviewTotal} pending</span></div>
          {reviewError && <p className="error" role="alert">{reviewError}</p>}
          {reviewLoading && !reviewItems.length ? <p role="status">Loading review queue…</p> : reviewItems.length || focusedReview ? <>
            {[...(focusedReview && !reviewItems.some(row => row.id === focusedReview.id) ? [focusedReview] : []), ...reviewItems].map(item => {
              const evidence = reviewMedia[item.id];
              const hasEvidence = item.has_media || item.has_video || item.has_uploaded_photo || item.has_image;
              return <article className="review-card" id={`review-${item.id}`} key={item.id}>
                <div className="review-copy"><strong>{item.id}</strong><span className={`staff-severity staff-severity-${(item.severity || "").toLowerCase()}`}>{item.severity || "Severity unavailable"}</span><p>{item.text}</p><small>{item.source === "citizen" ? "Citizen Portal" : item.source} · {item.created_at ? new Date(item.created_at).toLocaleString() : ""}</small><small>{[item.category, item.ward ? `Ward ${item.ward}` : null, item.location].filter(Boolean).join(" · ") || "Location unavailable"}</small></div>
                <div className="review-evidence">{evidence?.uploaded_photo_data && <img src={evidence.uploaded_photo_data} alt="Original uploaded complaint photo" />}{evidence?.video_data && <video controls preload="metadata" playsInline src={evidence.video_data}>Video playback is not supported.</video>}{!hasEvidence && <p>No original media was supplied.</p>}{evidence?.loaded && !evidence.uploaded_photo_data && !evidence.video_data && <p>No original media was returned.</p>}{evidence?.error && <p role="alert">{evidence.error}</p>}{hasEvidence && <button className="bmc-secondary-button" type="button" disabled={evidence?.loading || evidenceBusyCount >= 2} onClick={() => loadReviewEvidence(item.id)}>{evidence?.loading ? "Loading evidence…" : evidence?.error ? "Retry evidence" : evidence?.loaded ? "Reload evidence" : "Load original evidence"}</button>}</div>
                <div className="review-actions"><button type="button" disabled={reviewBusy[item.id]} onClick={() => decideReview(item, "accept")}>{reviewBusy[item.id] ? "Saving…" : "Accept"}</button><button className="bmc-danger-button" type="button" disabled={reviewBusy[item.id]} onClick={() => decideReview(item, "reject")}>Reject</button></div>
              </article>;
            })}
            {reviewItems.length < reviewTotal && <button className="staff-load-more bmc-secondary-button" type="button" disabled={reviewLoading} onClick={() => loadReviews(reviewItems.length, true)}>{reviewLoading ? "Loading…" : "Load more reviews"}</button>}
          </> : <p className="staff-register-empty">No complaints are waiting for review.</p>}
        </section>
      </div>
    </main>
  );

  return (
    <main id="top" className="bmc-portal">
      {staffHeader}

      <section className="bmc-stat-grid" aria-label="Complaint status totals">{[["Active accepted", overviewStats.active], ["Open", overviewStats.open], ["Inspection Scheduled", overviewStats.inspection_scheduled], ["Contractor Allotted", overviewStats.contractor_allotted], ["In Progress", overviewStats.in_progress], ["Resolved", overviewStats.resolved], ["Pending Review", overviewStats.pending_review], ["Rejected", overviewStats.rejected]].map(([label, value]) => <article className="bmc-stat-card" key={label}><span>{label}</span><strong>{value ?? "—"}</strong></article>)}</section>

      <section className={`bmc-map-layout ${pageView === "register" ? "is-register-view" : "is-overview-view"}`} aria-label={pageView === "register" ? "Complaint registration and map" : "Complaint map"}>
        <div className="map" id="complaint-map">
          <MapContainer center={[19.076, 72.878]} zoom={11}>
            <MapResizeObserver />
            <TileLayer
              attribution="© OpenStreetMap contributors"
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            {clusters.map((cluster) => (
              <CircleMarker
                key={cluster.cluster_id}
                center={[cluster.centroid.lat, cluster.centroid.lng]}
                radius={Math.min(18, 5 + cluster.size / 2)}
                pathOptions={{ color: tone[cluster.top_ranked_complaint.severity] }}
                eventHandlers={{ click: () => openCluster(cluster) }}
              >
                <Popup>
                  Cluster {cluster.cluster_id} · {cluster.size} active reports · {cluster.dominant_category}
                </Popup>
                <Tooltip direction="top" sticky>
                  Cluster {cluster.cluster_id} · {cluster.size} active reports
                </Tooltip>
              </CircleMarker>
            ))}
          </MapContainer>
        </div>

        <aside className="bmc-live-complaint" aria-hidden={pageView !== "register"} inert={pageView !== "register"}>
          <div className="bmc-live-complaint-heading"><span className="bmc-live-icon" aria-hidden="true">+</span><div><h2>Live complaint</h2><p>Add a report directly to BMC operations.</p></div></div>
          <form onSubmit={submit}>
            <textarea
              aria-label="Complaint description"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Describe the civic issue in any language…"
            />
            <fieldset className="media-fields">
              <legend>Photos &amp; Videos</legend>
              <label htmlFor="complaint-photo">Add photo</label>
              <input ref={photoInputRef} id="complaint-photo" type="file" accept="image/*" onChange={(event) => setPhoto(event.target.files[0] || null)} />
              <label htmlFor="complaint-video">Add video</label>
              <input ref={videoInputRef} id="complaint-video" type="file" accept="video/*" onChange={(event) => setVideo(event.target.files[0] || null)} />
            </fieldset>
            <button className="bmc-primary-button" type="submit" disabled={busy}>
              {busy ? (video ? "Processing video… please wait" : "Scoring…") : "Submit & place on map"}
            </button>
          </form>
          {message && <p className={message.type} role={message.type === "error" ? "alert" : "status"}>{message.text}</p>}
        </aside>
      </section>

      <section className="bmc-register-section" aria-labelledby="staff-register-title">
        <div className="staff-register-heading"><div className="bmc-register-heading-copy"><h2 id="staff-register-title">Complaint register</h2><p>Search individual reports and open their cluster details.</p></div><span className="bmc-register-result-count">{register.total} results</span></div>
        <div className="staff-register-filters"><label>Search<input type="search" value={registerFilters.q} placeholder="ID, description, category, ward or location" onChange={(event) => setRegisterFilters({ ...registerFilters, q: event.target.value })} /></label><label>Status<select value={registerFilters.status} onChange={(event) => setRegisterFilters({ ...registerFilters, status: event.target.value })}><option value="">All statuses</option><option value="open">Open</option><option value="inspection_scheduled">Inspection Scheduled</option><option value="contractor_allotted">Contractor Allotted</option><option value="in_progress">In Progress</option><option value="resolved">Resolved</option></select></label><label>Category<select value={registerFilters.category} onChange={(event) => setRegisterFilters({ ...registerFilters, category: event.target.value })}><option value="">All categories</option>{(register.categories || []).map((category) => <option key={category}>{category}</option>)}</select></label><label>Severity<select value={registerFilters.severity} onChange={(event) => setRegisterFilters({ ...registerFilters, severity: event.target.value })}><option value="">All severities</option><option>High</option><option>Medium</option><option>Low</option></select></label><button className="bmc-secondary-button" type="button" onClick={() => setRegisterFilters({ q: "", status: "", category: "", severity: "" })}>Clear filters</button><button className="bmc-secondary-button" type="button" onClick={() => loadRegister()} disabled={registerLoading}>{registerLoading ? "Refreshing…" : "Refresh"}</button></div>
        {registerError && <p className="error" role="alert">{registerError}</p>}
        {registerLoading && !register.items.length ? <p role="status">Loading complaints…</p> : register.items.length ? <><div className="staff-register-list">{register.items.map((complaint) => { const statusOptions = [complaint.status, ...(nextStaffStatuses[complaint.status] || [])]; return <article className="staff-register-row" key={complaint.id}><button className="staff-register-open" type="button" onClick={() => openComplaint(complaint)}><b>{complaint.id}</b><span>{complaint.text}</span><small>{complaint.created_at ? new Date(complaint.created_at).toLocaleString() : ""} · {complaint.category || "Uncategorised"} · Ward {complaint.ward || "—"} · {complaint.location || "Location unavailable"}</small></button><span className={`staff-severity staff-severity-${(complaint.severity || "").toLowerCase()}`}>{complaint.severity}</span><select aria-label={`Status for ${complaint.id}`} disabled={Boolean(statusBusy[complaint.id])} value={complaint.status} onChange={(event) => updateStatus(complaint, event.target.value)}>{statusOptions.map((value) => <option value={value} key={value}>{value.replaceAll("_", " ")}</option>)}</select></article>; })}</div>{register.items.length < register.total && <button className="staff-load-more" type="button" disabled={registerLoading} onClick={() => loadRegister(registerFilters, register.items.length, true)}>{registerLoading ? "Loading…" : "Load more complaints"}</button>}</> : <p className="staff-register-empty">{registerError ? "Complaint results could not be loaded." : "No complaints match these filters."}</p>}
      </section>

      {selected && (
        <section className="panel">
          <button
            className="close-panel"
            onClick={() => {
              setSelected(null);
              setClusterDetail(null);
            }}
          >
            ×
          </button>
          <h2>{selected.size} reports · {selected.dominant_category}</h2>

          {!clusterDetail && <p>Loading reports…</p>}
          {clusterDetail && (
            <div className="report-list">
              {clusterDetail.map((report) => (
                <article className="report-card" key={report.id}>
                  <span
                    className="severity-badge"
                    style={{ backgroundColor: tone[report.severity] }}
                  >
                    {report.severity}
                  </span>
                  <p>
                    <strong>Complaint text:</strong>{" "}
                    {report.text || "(no caption provided)"}
                  </p>
                  {(report.has_media || report.has_video || report.has_image) && (
                    <section className="private-evidence">
                      {!evidenceByComplaint[report.id] && <button type="button" onClick={() => loadEvidence(report.id)}>{staffToken ? "View private evidence" : "Sign in to view private evidence"}</button>}
                      {evidenceByComplaint[report.id]?.loading && <p role="status">Loading private evidence…</p>}
                      {evidenceByComplaint[report.id]?.error && <p className="error" role="alert">{evidenceByComplaint[report.id].error}</p>}
                      {evidenceByComplaint[report.id] && !evidenceByComplaint[report.id].loading && !evidenceByComplaint[report.id].error && (
                        <div className="evidence-items">
                          {evidenceByComplaint[report.id].uploaded_photo_data && <figure><img src={evidenceByComplaint[report.id].uploaded_photo_data} alt="Original uploaded complaint photo" /><figcaption>Uploaded photo</figcaption></figure>}
                          {evidenceByComplaint[report.id].video_data && <video controls controlsList="nodownload" preload="metadata" playsInline src={evidenceByComplaint[report.id].video_data}>Your browser cannot play this video evidence.</video>}
                          {evidenceByComplaint[report.id].has_video && evidenceByComplaint[report.id].representative_frame_data && <figure className="representative-frame"><img src={evidenceByComplaint[report.id].representative_frame_data} alt="Representative frame selected from the video" /><figcaption>Representative video frame</figcaption></figure>}
                          {evidenceByComplaint[report.id].legacy_external_media_unavailable && <p className="media-unavailable">This legacy record references externally hosted evidence that was not cached by the application. The external URL is withheld because it cannot be protected by staff authentication.</p>}
                          {evidenceByComplaint[report.id].transcript && <p><strong>Transcript:</strong> {evidenceByComplaint[report.id].transcript}</p>}
                        </div>
                      )}
                    </section>
                  )}
                  <small>{report.explanation}</small>
                </article>
              ))}
              {clusterDetail.length === 0 && <p>No reports could be loaded.</p>}
            </div>
          )}
        </section>
      )}
    </main>
  );
}


createRoot(document.getElementById("root")).render(<App />);
