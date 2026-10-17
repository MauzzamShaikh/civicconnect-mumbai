import { useEffect, useState } from "react";
import { PortalHeader } from "./CitizenComplaints.jsx";
import { apiRequest, TOKEN_KEY } from "./api.js";
import { formatDate, localizedHistoryEvent, localizedStatus, translate } from "./i18n.js";

export default function TrackPage({ user, navigate, onLogout, language = "en", onLanguageChange }) {
  const t = (key) => translate(language, key);
  const initial = new URLSearchParams(window.location.search).get("reference") || "";
  const [reference, setReference] = useState(initial);
  const [item, setItem] = useState(null);
  const [evidence, setEvidence] = useState(null);
  const [evidenceError, setEvidenceError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [follow, setFollow] = useState(false);
  const [isOwner, setIsOwner] = useState(false);
  const [followBusy, setFollowBusy] = useState(false);
  const [message, setMessage] = useState("");

  const track = async (value = reference) => {
    const cleanReference = value.trim();
    if (!/^CMP-[A-Z0-9]{10}$/i.test(cleanReference)) { setError(t("Enter a valid complaint reference, such as CMP-1234567890.")); return; }
    setBusy(true); setError(""); setItem(null); setEvidence(null); setEvidenceError(""); setMessage("");
    try {
      const token = sessionStorage.getItem(TOKEN_KEY);
      const data = await apiRequest(`/citizen/track/${encodeURIComponent(cleanReference)}`, { token });
      setItem(data);
      window.history.replaceState({}, "", `/track?reference=${encodeURIComponent(data.reference)}`);
      const state = await apiRequest(`/citizen/follows/${encodeURIComponent(data.reference)}`, { token });
      setFollow(state.active); setIsOwner(state.is_owner);
      if (state.is_owner) {
        try { setEvidence(await apiRequest(`/citizen/complaints/${encodeURIComponent(data.reference)}/media`, { token })); }
        catch (failure) { setEvidenceError(failure.message || t("Evidence could not be loaded.")); }
      }
    } catch (failure) {
      setError(failure.message);
      if (failure.message.includes("session has expired")) { sessionStorage.setItem("afterLogin", `/track?reference=${encodeURIComponent(cleanReference)}`); sessionStorage.removeItem(TOKEN_KEY); navigate("/login"); }
    } finally { setBusy(false); }
  };

  useEffect(() => { if (initial) track(initial); }, []);

  const saveFollow = async (active) => {
    const token = sessionStorage.getItem(TOKEN_KEY);
    setFollowBusy(true); setMessage("");
    try {
      const result = await apiRequest(`/citizen/follows/${encodeURIComponent(item.reference)}`, { method: "PUT", body: { active }, token });
      setFollow(result.active); setMessage(result.active ? t("You’ll receive updates about this complaint.") : t("You have unfollowed this complaint."));
    } catch (failure) {
      setMessage(failure.message);
      if (failure.message.includes("session has expired")) { sessionStorage.setItem("afterLogin", `/track?reference=${encodeURIComponent(item.reference)}`); sessionStorage.removeItem(TOKEN_KEY); navigate("/login"); }
    } finally { setFollowBusy(false); }
  };

  return <main className="dashboard-shell">
    <PortalHeader user={user} navigate={navigate} onLogout={onLogout} active="track" language={language} onLanguageChange={onLanguageChange} />
    <div className="dashboard-content">
      <div className="dashboard-crumb"><span>{t("Citizen portal")}</span><span>/</span><b>{t("Track Complaint")}</b></div>
      <section className="activity-card">
        <div className="citizen-page-heading"><div><span className="eyebrow">{t("YOUR CIVIC ACTIVITY")}</span><h1>{t("Track a complaint")}</h1><p>{t("Enter the complaint reference to see its current status and history.")}</p></div></div>
        <form className="report-filters track-search-form" onSubmit={event => { event.preventDefault(); track(); }}>
          <label className="report-search-label" htmlFor="reference">{t("Complaint reference")}<input id="reference" value={reference} onChange={event => setReference(event.target.value)} placeholder="CMP-1234567890" required /></label>
          <button className="secondary-button" disabled={busy}>{busy ? t("Searching…") : t("Track Complaint")}</button>
        </form>
        {error && <div className="citizen-alert citizen-alert-error" role="alert">{t(error)}</div>}
        {busy && <p className="complaint-loading" role="status">{t("Loading complaint…")}</p>}
        {item && <section className="complaint-detail-card" aria-live="polite">
          <div className="detail-title-row"><div><span className="eyebrow">{t("COMPLAINT REFERENCE")}</span><h2>{item.reference}</h2></div><span className={`citizen-status status-${item.status.replaceAll(/[^a-z0-9]+/gi, "-")}`}><span />{localizedStatus(language, item.status)}</span></div>
          <section className="tracking-complaint-description"><h2>{t("Complaint details")}</h2><p>{item.text || t("No description was provided.")}</p></section>
          <div className="detail-facts"><div className="detail-fact"><span><small>{t("Submitted")}</small><b>{formatDate(item.created_at, language)}</b></span></div><div className="detail-fact"><span><small>{t("Category")}</small><b>{item.category ? t(item.category) : t("Not assigned")}</b></span></div><div className="detail-fact"><span><small>{t("Ward / locality:")}</small><b>{[item.ward, item.location].filter(Boolean).join(" · ") || t("Not available")}</b></span></div></div>
          {isOwner && <section className="tracking-owner-evidence"><h2>{t("Evidence")}</h2>{evidenceError && <p role="alert">{t(evidenceError)}</p>}{evidence?.uploaded_photo_data && <img src={evidence.uploaded_photo_data} alt={t("Uploaded complaint photo")} />}{!evidence?.uploaded_photo_data && !evidence?.video_data && !evidenceError && <p>{t("No uploaded photo or video is available.")}</p>}{evidence?.video_data && <video src={evidence.video_data} controls preload="metadata" playsInline />}</section>}
          <div className="tracking-follow-control">{isOwner ? <p role="status">{t("You already receive updates as the complaint reporter.")}</p> : <label><input type="checkbox" checked={follow} disabled={followBusy} onChange={event => saveFollow(event.target.checked)} /> {t("Notify me about the progress of this complaint")}</label>}{message && <p role="status">{t(message)}</p>}</div>
          <section className="tracking-timeline"><h2>{t("Status timeline")}</h2>{(item.timeline || []).map((event, index) => <article className="tracking-event" key={`${event.event}-${index}`}><span className="tracking-dot" /><div><b>{localizedHistoryEvent(language, event.event)}{event.status ? ` · ${localizedStatus(language, event.status)}` : ""}</b><small>{formatDate(event.created_at, language)}</small></div></article>)}</section>
        </section>}
      </section>
    </div>
  </main>;
}
