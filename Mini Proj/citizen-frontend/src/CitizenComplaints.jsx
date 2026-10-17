import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft, ArrowRight, Building2, CheckCircle2, Clock3, FileText, Image as ImageIcon,
  LoaderCircle, LogOut, MapPin, Plus, RefreshCw, ShieldCheck, Video, X,
} from "lucide-react";
import { CircleMarker, MapContainer, TileLayer, useMapEvents } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { apiRequest, TOKEN_KEY } from "./api.js";
import NotificationBell from "./NotificationBell.jsx";
import { LanguageSelector, formatDate, localizedHistoryEvent, localizedSeverity, localizedStatus, translate } from "./i18n.js";

const token = () => sessionStorage.getItem(TOKEN_KEY);
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_VIDEO_BYTES = 55 * 1024 * 1024;
const SUPPORTED_IMAGES = ["image/jpeg", "image/png", "image/webp", "image/gif"];
const SUPPORTED_VIDEOS = ["video/mp4", "video/quicktime", "video/webm"];

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("The selected file could not be read. Please choose it again."));
    reader.onabort = () => reject(new Error("File reading was cancelled."));
    reader.readAsDataURL(file);
  });
}

function prettyStatus(value = "unknown", language = "en") {
  return localizedStatus(language, value);
}

function Brand({ navigate, language }) {
  return <a className="brand brand-compact" href="/dashboard" onClick={(event) => { event.preventDefault(); navigate("/dashboard"); }}>
    <span className="brand-mark" aria-hidden="true"><Building2 size={20} strokeWidth={1.8} /><span /></span>
    <span className="brand-copy"><strong>BMC Civic</strong><small>{translate(language, "Citizen services")}</small></span>
  </a>;
}

export function PortalHeader({ user, navigate, onLogout, active, language, onLanguageChange }) {
  const t = (key) => translate(language, key);
  const [menuOpen, setMenuOpen] = useState(false);
  return <header className="dashboard-header">
    <Brand navigate={navigate} language={language} />
    <nav className="dashboard-nav" aria-label={t("Main navigation")}>
      <a className={active === "overview" ? "nav-active" : ""} href="/dashboard" onClick={(event) => { event.preventDefault(); navigate("/dashboard"); }}>{t("Overview")}</a>
      <a className={active === "reports" ? "nav-active" : ""} href="/complaints" onClick={(event) => { event.preventDefault(); navigate("/complaints"); }}>{t("My reports")}</a>
      <span className="nav-disabled" aria-label={t("Updates, coming in a future phase")}>{t("Updates")} <span>{t("Coming soon")}</span></span>
      <a href="/track" onClick={(event) => { event.preventDefault(); navigate("/track"); }}>{t("Track Complaint")}</a>
      <a href="/notifications" onClick={(event) => { event.preventDefault(); navigate("/notifications"); }}>{t("Notifications")}</a>
    </nav>
    <div className="profile-area"><LanguageSelector language={language} onChange={onLanguageChange} compact /><NotificationBell navigate={navigate} language={language} /><div className="profile-menu-wrap">
      <button className="profile-button" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}><span className="avatar">{user.name?.trim()?.charAt(0)?.toUpperCase() || "C"}</span><span className="profile-name">{user.name}</span><span aria-hidden="true">⌄</span></button>
      {menuOpen && <div className="profile-dropdown"><div className="dropdown-user"><b>{user.name}</b><small>{user.email}</small></div><button onClick={onLogout}><LogOut size={16} /> {t("Sign out")}</button></div>}
    </div></div>
  </header>;
}

function MapClickCapture({ onSelect }) {
  useMapEvents({ click(event) { onSelect([event.latlng.lat, event.latlng.lng]); } });
  return null;
}

function LocationPicker({ value, onChange, language }) {
  const t = (key) => translate(language, key);
  const center = value || [19.076, 72.878];
  const bounds = useMemo(() => L.latLngBounds([18.80, 72.70], [19.35, 73.10]), []);
  return <div className="location-picker">
    <div className="location-picker-copy"><span className="field-label">{t("Optional location pin")}</span><p>{t("Click the map to mark the incident. Without a pin, location will be resolved from your description.")}</p></div>
    <MapContainer className="citizen-map" center={center} zoom={value ? 15 : 11} maxBounds={bounds} maxBoundsViscosity={0.8} scrollWheelZoom={false}>
      <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
      <MapClickCapture onSelect={onChange} />
      {value && <CircleMarker center={value} radius={9} pathOptions={{ color: "#fff", weight: 3, fillColor: "#087d78", fillOpacity: 1 }} />}
    </MapContainer>
    <div className="pin-summary">{value ? <><MapPin size={16} /><span>{t("Pin selected: {lat}, {lng}", { lat: value[0].toFixed(5), lng: value[1].toFixed(5) })}</span><button type="button" onClick={() => onChange(null)}>{t("Clear pin")}</button></> : <><MapPin size={16} /><span>{t("No pin selected — this is optional")}</span></>}</div>
  </div>;
}

function StatusPill({ status, language }) {
  return <span className={`citizen-status status-${(status || "unknown").replaceAll(/[^a-z0-9]+/gi, "-").toLowerCase()}`}><span />{prettyStatus(status, language)}</span>;
}

function CitizenComplaints({ path, user, navigate, onLogout, language, onLanguageChange }) {
  const t = (key, values) => translate(language, key, values);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState({ q: "", status: "", category: "", sort: "newest" });
  const [categories, setCategories] = useState([]);
  const route = path === "/complaints/new" ? "new" : path === "/complaints" ? "list" : "detail";
  const complaintId = route === "detail" ? decodeURIComponent(path.slice("/complaints/".length)) : "";
  const loadComplaints = async (selected = filters, offset = 0, append = false) => {
    setLoading(true); setError("");
    try {
      const params = new URLSearchParams();
      Object.entries(selected).forEach(([key, value]) => { if (value) params.set(key, value); });
      params.set("limit", "50"); params.set("offset", String(offset));
      const result = await apiRequest(`/citizen/complaints?${params}`, { token: token() });
      setItems((current) => append ? [...current, ...(result.items || [])] : (result.items || [])); setTotal(result.total || 0); setCategories(result.categories || []);
    }
    catch (failure) { setError(failure.message); }
    finally { setLoading(false); }
  };
  useEffect(() => {
    if (route !== "list") return;
    const timer = window.setTimeout(() => loadComplaints(filters), 250);
    return () => window.clearTimeout(timer);
  }, [route, filters.q, filters.status, filters.category, filters.sort]);

  return <main className="dashboard-shell citizen-complaints-shell">
    <PortalHeader user={user} navigate={navigate} onLogout={onLogout} active={route === "new" ? "new" : "reports"} language={language} onLanguageChange={onLanguageChange} />
    <div className="dashboard-content citizen-workspace">
      <div className="dashboard-crumb"><span>{t("Citizen portal")}</span><span>/</span><a href="/dashboard" onClick={(event) => { event.preventDefault(); navigate("/dashboard"); }}>{t("Overview")}</a><span>/</span><b>{t(route === "new" ? "Report a civic issue" : route === "list" ? "My reports" : "Complaint details")}</b></div>
      {route === "new" && <SubmissionForm navigate={navigate} language={language} />}
      {route === "list" && <ComplaintList items={items} total={total} loading={loading} error={error} reload={() => loadComplaints(filters)} loadMore={() => loadComplaints(filters, items.length, true)} navigate={navigate} filters={filters} setFilters={setFilters} categories={categories} language={language} />}
      {route === "detail" && <ComplaintDetail complaintId={complaintId} navigate={navigate} language={language} />}
    </div>
  </main>;
}

function SubmissionForm({ navigate, language }) {
  const t = (key, values) => translate(language, key, values);
  const [description, setDescription] = useState("");
  const [image, setImage] = useState(null);
  const [video, setVideo] = useState(null);
  const [pin, setPin] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState(null);
  const [imagePreview, setImagePreview] = useState("");
  const [videoPreview, setVideoPreview] = useState("");
  const imageInputId = "citizen-image";
  const videoInputId = "citizen-video";

  useEffect(() => {
    if (!image) { setImagePreview(""); return; }
    const url = URL.createObjectURL(image);
    setImagePreview(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);
  useEffect(() => {
    if (!video) { setVideoPreview(""); return; }
    const url = URL.createObjectURL(video);
    setVideoPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [video]);

  const chooseFile = (kind) => (event) => {
    const file = event.target.files?.[0] || null;
    setError("");
    if (!file) { (kind === "image" ? setImage : setVideo)(null); return; }
    const imageFile = kind === "image";
    const allowed = imageFile ? SUPPORTED_IMAGES : SUPPORTED_VIDEOS;
    const max = imageFile ? MAX_IMAGE_BYTES : MAX_VIDEO_BYTES;
    if (!allowed.includes(file.type)) {
      setError(t(imageFile ? "Choose a supported image (JPEG, PNG, WebP or GIF)." : "Choose a supported video (MP4, MOV or WebM)."));
      event.target.value = "";
      return;
    }
    if (file.size > max) {
      setError(t(imageFile ? "Images must be 10 MB or smaller." : "Videos must be 55 MB or smaller."));
      event.target.value = "";
      return;
    }
    (imageFile ? setImage : setVideo)(file);
  };

  const submit = async (event) => {
    event.preventDefault(); setError(""); setCreated(null);
    if (description.trim().length < 5) { setError(t("Please describe the issue in at least 5 characters.")); return; }
    setBusy(true);
    try {
      const [imageBase64, videoBase64] = await Promise.all([
        image ? fileToDataUrl(image) : Promise.resolve(null),
        video ? fileToDataUrl(video) : Promise.resolve(null),
      ]);
      const body = { text: description.trim(), image_base64: imageBase64, video_base64: videoBase64 };
      if (pin) { body.lat = pin[0]; body.lng = pin[1]; }
      const response = await apiRequest("/citizen/complaints", { method: "POST", body, token: token() });
      const complaint = response.complaint || { id: response.id, status: response.status };
      if (!complaint.id) throw new Error(t("The submission was received but the complaint reference was missing."));
      setCreated(complaint);
      setDescription(""); setImage(null); setVideo(null); setPin(null);
      document.getElementById(imageInputId).value = "";
      document.getElementById(videoInputId).value = "";
    } catch (failure) { setError(t(failure.message || "Your complaint could not be submitted. Please try again.")); }
    finally { setBusy(false); }
  };

  return <>
    <div className="citizen-page-heading"><div><span className="eyebrow">{t("CITIZEN SERVICES")}</span><h1>{t("Report a civic issue")}</h1><p>{t("Describe what’s happening so the BMC team can assess your report. A location pin is optional.")}</p></div><button className="secondary-button" onClick={() => navigate("/complaints")}><ArrowLeft size={16} /> {t("My reports")}</button></div>
    {created && <div className="citizen-alert citizen-alert-success" role="status"><CheckCircle2 size={20} /><div><b>{t("Complaint submitted")}</b><p>{t("Reference {id} · Current status: {status}", { id: created.id, status: prettyStatus(created.status || "open", language) })}</p><button onClick={() => navigate(`/complaints/${encodeURIComponent(created.id)}`)}>{t("View complaint")} <ArrowRight size={15} /></button></div></div>}
    {error && <div className="citizen-alert citizen-alert-error" role="alert"><X size={19} /><span>{t(error)}</span></div>}
    <form className="citizen-form-card" onSubmit={submit}>
      <section className="citizen-form-section"><div className="citizen-section-heading"><span className="section-icon"><FileText size={18} /></span><div><h2>{t("Issue details")}</h2><p>{t("Include what happened and where you noticed it.")}</p></div></div>
        <label className="field-label" htmlFor="complaint-description">{t("Complaint description")} <span>{t("Required")}</span></label>
        <textarea id="complaint-description" className="citizen-textarea" value={description} onChange={(event) => setDescription(event.target.value)} minLength={5} maxLength={10000} required placeholder={t("For example: A large pothole has formed near the bus stop and is difficult for vehicles to avoid.")} />
        <div className="textarea-foot"><span>{t("Do not include passwords or sensitive personal information.")}</span><span>{description.length}/10,000</span></div>
      </section>
      <section className="citizen-form-section"><div className="citizen-section-heading"><span className="section-icon"><ImageIcon size={18} /></span><div><h2>{t("Photos & videos")} <small>{t("Optional")}</small></h2><p>{t("You can include one photo and one video in the same report. Each is processed and retained separately.")}</p></div></div>
        <div className="upload-grid">
          <div className="upload-card"><label className="field-label" htmlFor={imageInputId}><ImageIcon size={15} /> {t("Add a photo")}</label><input id={imageInputId} type="file" accept="image/jpeg,image/png,image/webp,image/gif" onChange={chooseFile("image")} /><small>{t("JPEG, PNG, WebP or GIF · up to 10 MB")}</small>{image && <div className="selected-file"><span>{image.name} · {(image.size / 1048576).toFixed(1)} MB</span><button type="button" aria-label={t("Remove photo")} onClick={() => { setImage(null); document.getElementById(imageInputId).value = ""; }}><X size={15} /></button></div>}{imagePreview && <img className="upload-preview-image" src={imagePreview} alt={t("Selected complaint photo preview")} />}</div>
          <div className="upload-card"><label className="field-label" htmlFor={videoInputId}><Video size={15} /> {t("Add a video")}</label><input id={videoInputId} type="file" accept="video/mp4,video/quicktime,video/webm" onChange={chooseFile("video")} /><small>{t("MP4, MOV or WebM · up to 55 MB")}</small>{video && <div className="selected-file"><span>{video.name} · {(video.size / 1048576).toFixed(1)} MB</span><button type="button" aria-label={t("Remove video")} onClick={() => { setVideo(null); document.getElementById(videoInputId).value = ""; }}><X size={15} /></button></div>}{videoPreview && <video className="upload-preview-video" src={videoPreview} controls preload="metadata" />}</div>
        </div>
      </section>
      <section className="citizen-form-section"><div className="citizen-section-heading"><span className="section-icon"><MapPin size={18} /></span><div><h2>{t("Where is the issue?")} <small>{t("Optional")}</small></h2><p>{t("Choose a map pin to use that location for this report.")}</p></div></div><LocationPicker value={pin} onChange={setPin} language={language} /></section>
      <div className="citizen-submit-row"><span><ShieldCheck size={16} /> {t("Your report will be associated with your verified account.")}</span><button className="primary-button citizen-submit" type="submit" disabled={busy}>{busy ? <><LoaderCircle className="spin" size={18} /> {t("Submitting…")}</> : <>{t("Submit complaint")} <ArrowRight size={17} /></>}</button></div>
    </form>
  </>;
}

function ComplaintList({ items, total, loading, error, reload, loadMore, navigate, filters, setFilters, categories, language }) {
  const t = (key, values) => translate(language, key, values);
  return <>
    <div className="citizen-page-heading"><div><span className="eyebrow">{t("YOUR CIVIC ACTIVITY")}</span><h1>{t("My reports")}</h1><p>{t("Track the current BMC status of complaints submitted from your account.")}</p></div><div className="heading-actions"><button className="secondary-button" onClick={reload} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} /> {t("Refresh")}</button><button className="primary-button compact-primary" onClick={() => navigate("/complaints/new")}><Plus size={16} /> {t("Report a civic issue")}</button></div></div>
    <div className="report-filters"><label className="report-search-label">{t("Search reports")}<input type="search" value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} placeholder={t("Reference, description, category, ward or location")} /></label><label>{t("Status")}<select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}><option value="">{t("All statuses")}</option>{[["open", "Open"], ["inspection_scheduled", "Inspection Scheduled"], ["contractor_allotted", "Contractor Allotted"], ["in_progress", "In Progress"], ["resolved", "Resolved"], ["pending_review", "Pending Review"], ["rejected", "Rejected"]].map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}</select></label><label>{t("Category")}<select value={filters.category} onChange={(event) => setFilters({ ...filters, category: event.target.value })}><option value="">{t("All categories")}</option>{categories.map((category) => <option key={category} value={category}>{translate(language, category)}</option>)}</select></label><label>{t("Sort")}<select value={filters.sort} onChange={(event) => setFilters({ ...filters, sort: event.target.value })}><option value="newest">{t("Newest first")}</option><option value="oldest">{t("Oldest first")}</option></select></label><button className="secondary-button" onClick={() => setFilters({ q: "", status: "", category: "", sort: "newest" })}>{t("Clear filters")}</button></div>
    {error && <div className="citizen-alert citizen-alert-error" role="alert"><X size={19} /><span>{error}</span></div>}
    <p className="report-result-count">{loading ? t("Updating reports…") : `${total} ${t(total === 1 ? "report" : "reports")}`}</p>
    {loading && !items.length && <div className="complaint-loading" role="status"><LoaderCircle className="spin" size={24} /> {t("Loading your reports…")}</div>}
    {items.length > 0 && <><div className="complaint-list">{items.map((item) => <button className="complaint-list-card" key={item.id} onClick={() => navigate(`/complaints/${encodeURIComponent(item.id)}`)}>
      <span className="complaint-list-icon"><FileText size={20} /></span><span className="complaint-list-main"><span className="complaint-card-top"><b>{item.id}</b><StatusPill status={item.status} language={language} /></span><strong>{item.text}</strong><span className="complaint-meta">{formatDate(item.created_at, language)}{item.category ? ` · ${translate(language, item.category)}` : ""}{item.ward ? ` · ${t("Ward")} ${item.ward}` : ""}</span>{item.location && <span className="complaint-meta"><MapPin size={13} /> {item.location}</span>}</span>
      <span className="complaint-list-side">{item.severity && <span className={`severity-tag severity-${item.severity.toLowerCase()}`}>{localizedSeverity(language, item.severity)}</span>}{(item.has_uploaded_photo || item.has_video || item.has_media) && <span className="media-indicator">{item.has_uploaded_photo && <ImageIcon size={14} />}{item.has_video && <Video size={14} />}{t(item.has_uploaded_photo && item.has_video ? "Photo & video" : item.has_video ? "Video" : item.has_uploaded_photo ? "Photo" : "Evidence")}</span>}<ArrowRight size={17} /></span>
    </button>)}</div>{items.length < total && <button className="secondary-button load-more-reports" disabled={loading} onClick={loadMore}>{loading ? "Loading…" : "Load more reports"}</button>}</>}
    {!loading && !items.length && <div className="reports-empty"><span className="empty-icon"><FileText size={24} /></span><h2>{t(filters.q || filters.status || filters.category ? "No matching reports" : "No complaints yet")}</h2><p>{t(filters.q || filters.status || filters.category ? "Try changing or clearing the filters." : "When you report an issue, its reference and current BMC status will appear here.")}</p>{(filters.q || filters.status || filters.category) ? <button className="secondary-button" onClick={() => setFilters({ q: "", status: "", category: "", sort: "newest" })}>{t("Clear filters")}</button> : <button className="primary-button compact-primary" onClick={() => navigate("/complaints/new")}><Plus size={16} /> {t("Submit your first report")}</button>}</div>}
  </>;
}

function ComplaintDetail({ complaintId, navigate, language }) {
  const t = (key, values) => translate(language, key, values);
  const [complaint, setComplaint] = useState(null);
  const [media, setMedia] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    apiRequest(`/citizen/complaints/${encodeURIComponent(complaintId)}`, { token: token() })
      .then((item) => { if (active) setComplaint(item); return apiRequest(`/citizen/complaints/${encodeURIComponent(complaintId)}/media`, { token: token() }); })
      .then((content) => { if (active) setMedia(content); })
      .catch((failure) => { if (active) setError(failure.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [complaintId]);
  if (loading) return <div className="complaint-loading" role="status"><LoaderCircle className="spin" size={24} /> {t("Loading complaint details…")}</div>;
  if (error) return <><button className="back-link" onClick={() => navigate("/complaints")}><ArrowLeft size={15} /> {t("Back to my reports")}</button><div className="citizen-alert citizen-alert-error" role="alert"><X size={19} /><span>{t(error)}</span></div></>;
  if (!complaint) return null;
  return <>
    <button className="back-link" onClick={() => navigate("/complaints")}><ArrowLeft size={15} /> {t("Back to my reports")}</button>
    <section className="complaint-detail-card"><div className="detail-title-row"><div><span className="eyebrow">{t("COMPLAINT REFERENCE")}</span><h1>{complaint.id}</h1></div><StatusPill status={complaint.status} language={language} /></div>
      <p className="detail-description">{complaint.text}</p>
      {complaint.status === "rejected" && complaint.rejection_reason && <p className="citizen-alert citizen-alert-error">{t("Rejection reason")}: {complaint.rejection_reason}</p>}
      <div className="detail-facts"><Fact label={t("Submitted")} value={formatDate(complaint.created_at, language)} icon={Clock3} /><Fact label={t("Severity")} value={localizedSeverity(language, complaint.severity)} icon={ShieldCheck} /><Fact label={t("Category")} value={complaint.category ? translate(language, complaint.category) : t("Not assigned")} icon={FileText} /><Fact label={t("Ward")} value={complaint.ward || t("Not assigned")} icon={Building2} /><Fact label={t("Location")} value={complaint.location || (complaint.lat && complaint.lng ? `${complaint.lat}, ${complaint.lng}` : t("Resolved from description or unavailable"))} icon={MapPin} /></div>
      <section className="tracking-timeline"><h2>{t("Status timeline")}</h2>{(complaint.history || []).map((event, index) => <article className="tracking-event" key={event.id || `${event.event_type}-${index}`}><span className="tracking-dot" /><div><b>{localizedHistoryEvent(language, event.event_type)}{["status_changed", "review_accepted", "review_rejected"].includes(event.event_type) && event.to_status ? ` ${t("to")} ${prettyStatus(event.to_status === "accepted" ? "open" : event.to_status, language)}` : ""}</b><small>{t(event.actor_label || "BMC Team")}{event.created_at ? ` · ${formatDate(event.created_at, language)}` : ""}</small></div></article>)}</section>
      {(media?.image_data || media?.uploaded_photo_data || media?.video_data || media?.legacy_external_media_unavailable) && <section className="detail-media"><h2>{t("Evidence")}</h2>{media.uploaded_photo_data && <img src={media.uploaded_photo_data} alt={t("Uploaded complaint photo")} />}{!media.uploaded_photo_data && !media.has_video && media.image_data && <img src={media.image_data} alt={t("Uploaded complaint photo")} />}{media.video_data && <video src={media.video_data} controls preload="metadata" playsInline />}{media.has_video && media.image_data && <figure><img src={media.image_data} alt={t("Representative frame selected from the video")} /><figcaption>{t("Representative video frame")}</figcaption></figure>}{media.legacy_external_media_unavailable && <p className="citizen-alert citizen-alert-error">{t("This older complaint refers to externally hosted evidence that was not cached, so it cannot be securely displayed here.")}</p>}</section>}
      {media?.transcript && <section className="detail-transcript"><h2>{t("Video transcript")}</h2><p>{media.transcript}</p></section>}
    </section>
  </>;
}

function Fact({ label, value, icon: Icon }) {
  return <div className="detail-fact"><Icon size={16} /><span><small>{label}</small><b>{value}</b></span></div>;
}

export default CitizenComplaints;
