import { useEffect, useState } from "react";
import { apiRequest, TOKEN_KEY } from "./api.js";
import { PortalHeader } from "./CitizenComplaints.jsx";
import { formatDate, localizeNotification, translate } from "./i18n.js";

export default function CitizenNotifications({ user, navigate, onLogout, language, onLanguageChange }) {
  const [data, setData] = useState({ items: [], unread_count: 0 });
  const [loading, setLoading] = useState(true); const [error, setError] = useState("");
  const t = (key) => translate(language, key);
  const load = async () => { setLoading(true); setError(""); try { setData(await apiRequest("/citizen/notifications", { token: sessionStorage.getItem(TOKEN_KEY) })); } catch (e) { setError(t(e.message)); } finally { setLoading(false); } };
  useEffect(() => { load(); }, []);
  const mark = async (id) => { try { setData(await apiRequest(`/citizen/notifications/${id}/read`, { method: "PATCH", token: sessionStorage.getItem(TOKEN_KEY) })); } catch (e) { setError(t(e.message)); } };
  const all = async () => { try { setData(await apiRequest("/citizen/notifications/read-all", { method: "POST", token: sessionStorage.getItem(TOKEN_KEY) })); } catch (e) { setError(t(e.message)); } };
  return <main className="dashboard-shell"><PortalHeader user={user} navigate={navigate} onLogout={onLogout} active="notifications" language={language} onLanguageChange={onLanguageChange} /><section className="dashboard-content"><div className="activity-card"><div className="activity-heading"><div><span className="eyebrow">{t("YOUR CIVIC ACTIVITY")}</span><h1>{t("Notifications")}</h1></div><div><button onClick={load} disabled={loading}>{t("Refresh")}</button> <button onClick={all} disabled={!data.unread_count}>{t("Mark all as read")}</button></div></div>{error && <p role="alert">{error}</p>}{loading ? <p role="status">{t("Loading notifications…")}</p> : data.items.length ? data.items.map(n => { const copy = localizeNotification(language, n.title, n.summary); return <article className={`recent-report ${n.read ? "" : "unread"}`} key={n.id}><div><b>{copy.title}</b><p>{copy.summary}</p><small>{formatDate(n.created_at, language)}</small></div><button onClick={() => { mark(n.id); if (n.complaint_id) navigate(`/track?reference=${encodeURIComponent(n.complaint_id)}`); }}>{n.read ? t("Open complaint") : t("Mark as read")}</button></article>; }) : <p>{t("No notifications yet.")}</p>}</div></section></main>;
}
