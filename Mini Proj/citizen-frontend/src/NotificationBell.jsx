import { useEffect, useState } from "react";
import { Bell, Check, ExternalLink, LoaderCircle, RefreshCw } from "lucide-react";
import { apiRequest, TOKEN_KEY } from "./api.js";
import { formatDate, localizeNotification, translate } from "./i18n.js";

export default function NotificationBell({ role = "citizen", navigate, language = "en" }) {
  const t = (key) => translate(language, key);
  const [data, setData] = useState({ items: [], unread_count: 0 });
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const root = role === "admin" ? "/bmc/notifications" : "/citizen/notifications";
  const refresh = async () => {
    setLoading(true); setError("");
    try { setData(await apiRequest(root, { token: sessionStorage.getItem(role === "admin" ? "bmcStaffToken" : TOKEN_KEY) })); }
    catch (failure) { setError(t(failure.message || "Notifications could not be loaded.")); }
    finally { setLoading(false); }
  };
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 60000);
    return () => window.clearInterval(timer);
  }, [root]);
  const mark = async (id) => {
    try { setData(await apiRequest(`${root}/${id}/read`, { method: "PATCH", token: sessionStorage.getItem(role === "admin" ? "bmcStaffToken" : TOKEN_KEY) })); }
    catch (failure) { setError(t(failure.message || "The notification could not be updated.")); }
  };
  const markAll = async () => {
    try { setData(await apiRequest(`${root}/read-all`, { method: "POST", token: sessionStorage.getItem(role === "admin" ? "bmcStaffToken" : TOKEN_KEY) })); }
    catch (failure) { setError(t(failure.message || "Notifications could not be updated.")); }
  };
  return <div className="notification-wrap">
    <button className="notification-button" aria-label={t("Notifications, {count} unread").replace("{count}", String(data.unread_count))} aria-expanded={open} onClick={() => { setOpen((value) => !value); if (!open) refresh(); }}><Bell size={18} />{data.unread_count > 0 && <span className="notification-count">{data.unread_count > 99 ? "99+" : data.unread_count}</span>}</button>
    {open && <section className="notification-popover" aria-label={t("Notifications")}>
      <div className="notification-popover-head"><b>{t("Notifications")}</b><div><button type="button" title={t("Refresh notifications")} aria-label={t("Refresh notifications")} onClick={refresh} disabled={loading}><RefreshCw size={14} className={loading ? "spin" : ""} /></button><button type="button" onClick={markAll} disabled={!data.unread_count}>{t("Mark all read")}</button></div></div>
      {error && <p className="notification-error" role="alert">{error}</p>}
      {loading && !data.items.length ? <p className="notification-empty"><LoaderCircle size={16} className="spin" /> {t("Loading…")}</p> : data.items.length ? <div className="notification-items">{data.items.map((item) => <article className={`notification-item ${item.read ? "" : "notification-unread"}`} key={item.id}>
        <button type="button" className="notification-main" onClick={async () => { if (!item.read) await mark(item.id); setOpen(false); if (item.complaint_id) navigate(role === "admin" ? `/complaints/${encodeURIComponent(item.complaint_id)}` : `/track?reference=${encodeURIComponent(item.complaint_id)}`); }}>{(() => { const message = localizeNotification(language, item.title, item.summary); return <><b>{message.title}</b><span>{message.summary}</span></>; })()}<time>{formatDate(item.created_at, language)}</time></button>
        {!item.read && <button className="notification-read" aria-label={t("Mark as read")} title={t("Mark as read")} onClick={() => mark(item.id)}><Check size={15} /></button>}
      </article>)}</div> : <p className="notification-empty">{t("All caught up. New updates will appear here.")}</p>}
      {data.items.length > 0 && <p className="notification-foot"><ExternalLink size={13} /> {t("Updates are refreshed automatically every minute.")}</p>}
      <button type="button" className="notification-view-all" onClick={() => { setOpen(false); navigate("/notifications"); }}>{t("View all notifications")}</button>
    </section>}
  </div>;
}
