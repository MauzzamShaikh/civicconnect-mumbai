import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft, ArrowRight, BadgeCheck, Bell, Building2, Check, ChevronDown,
  CircleAlert, CircleCheck, Clock3, Eye, EyeOff, FileText, KeyRound,
  Leaf, LoaderCircle, LockKeyhole, LogOut, Mail, MapPin, Menu, ShieldCheck,
  UserRound, Users, Waves, RefreshCw,
} from "lucide-react";
import CitizenComplaints from "./CitizenComplaints.jsx";
import NotificationBell from "./NotificationBell.jsx";
import TrackPage from "./TrackPage.jsx";
import CitizenNotifications from "./CitizenNotifications.jsx";
import { apiRequest, TOKEN_KEY } from "./api.js";
import { LANGUAGE_KEY, LanguageSelector, formatDate, localizedHistoryEvent, localizedStatus, translate } from "./i18n.js";

function App() {
  const [path, setPath] = useState(window.location.pathname || "/login");
  const [pendingEmail, setPendingEmail] = useState("");
  const [user, setUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [language, setLanguage] = useState(() => {
    const saved = localStorage.getItem(LANGUAGE_KEY);
    return ["en", "mr", "hi"].includes(saved) ? saved : "en";
  });
  const changeLanguage = (next) => { setLanguage(next); localStorage.setItem(LANGUAGE_KEY, next); };
  const t = (key, values) => translate(language, key, values);

  const navigate = useCallback((next) => {
    window.history.pushState({}, "", next);
    setPath(window.location.pathname);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  useEffect(() => {
    const onPopState = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (path !== "/dashboard" && path !== "/notifications" && path !== "/track" && !path.startsWith("/complaints")) return;
    const token = sessionStorage.getItem(TOKEN_KEY);
    if (!token) {
      if (path === "/track") sessionStorage.setItem("afterLogin", path + window.location.search);
      navigate("/login");
      return;
    }
    let cancelled = false;
    setAuthLoading(true);
    apiRequest("/auth/me", { token })
      .then((currentUser) => { if (!cancelled) setUser(currentUser); })
      .catch(() => {
        sessionStorage.removeItem(TOKEN_KEY);
        if (!cancelled) {
          if (path === "/track") sessionStorage.setItem("afterLogin", `${path}${window.location.search}`);
          navigate("/login");
        }
      })
      .finally(() => { if (!cancelled) setAuthLoading(false); });
    return () => { cancelled = true; };
  }, [path, navigate]);

  const logout = () => {
    sessionStorage.removeItem(TOKEN_KEY);
    setUser(null);
    navigate("/login");
  };

  if (path === "/dashboard" || path === "/notifications" || path === "/track" || path.startsWith("/complaints")) {
    return authLoading || !user
      ? <LoadingScreen label={t("Opening your citizen dashboard")} />
      : path === "/dashboard"
        ? <Dashboard user={user} onLogout={logout} navigate={navigate} language={language} onLanguageChange={changeLanguage} />
        : path === "/notifications" ? <CitizenNotifications user={user} navigate={navigate} onLogout={logout} language={language} onLanguageChange={changeLanguage} />
        : path === "/track" ? <TrackPage user={user} navigate={navigate} onLogout={logout} language={language} onLanguageChange={changeLanguage} />
        : <CitizenComplaints path={path} user={user} onLogout={logout} navigate={navigate} language={language} onLanguageChange={changeLanguage} />;
  }

  const page = path === "/register" ? "register"
    : path === "/verify-email" ? "verify"
      : path === "/forgot-password" ? "forgot"
        : path === "/reset-password" ? "reset" : "login";

  return (
    <AuthLayout page={page} language={language} onLanguageChange={changeLanguage}>
      {page === "register" && <RegisterPage navigate={navigate} onPendingEmail={setPendingEmail} language={language} />}
      {page === "login" && <LoginPage navigate={navigate} onLogin={(nextUser) => { setUser(nextUser); const next = sessionStorage.getItem("afterLogin") || "/dashboard"; sessionStorage.removeItem("afterLogin"); navigate(next); }} language={language} />}
      {page === "verify" && <VerifyPage navigate={navigate} initialEmail={pendingEmail} language={language} />}
      {page === "forgot" && <ForgotPage navigate={navigate} language={language} />}
      {page === "reset" && <ResetPage navigate={navigate} language={language} />}
    </AuthLayout>
  );
}

function Brand({ compact = false, onClick, language = "en" }) {
  const handleClick = (event) => {
    event.preventDefault();
    if (onClick) {
      onClick();
      return;
    }
    window.history.pushState({}, "", "/login");
    window.dispatchEvent(new PopStateEvent("popstate"));
  };
  return (
    <a className={`brand ${compact ? "brand-compact" : ""}`} href={onClick ? "/dashboard" : "/login"} onClick={handleClick}>
      <span className="brand-mark" aria-hidden="true"><Building2 size={20} strokeWidth={1.8} /><span /></span>
      <span className="brand-copy"><strong>BMC Civic</strong><small>{translate(language, "Citizen services")}</small></span>
    </a>
  );
}

function CivicIllustration({ page, language }) {
  const t = (key) => translate(language, key);
  const copy = {
    register: ["A better city begins", "with every citizen."],
    login: ["Your voice helps", "Mumbai move forward."],
    verify: ["One small step", "towards a better city."],
    forgot: ["Your civic journey", "is always within reach."],
    reset: ["Keep your account", "safe and in your hands."],
  }[page];
  return (
    <section className="visual-panel" aria-label={t("Civic services illustration")}>
      <div className="visual-top"><Brand language={language} /><span className="service-tag"><ShieldCheck size={14} /> {t("Secure citizen access")}</span></div>
      <div className="visual-copy">
        <span className="eyebrow"><span className="eyebrow-dot" /> {t("A CITY THAT LISTENS")}</span>
        <h1>{t(copy[0])}<br /><em>{t(copy[1])}</em></h1>
        <p>{t("Connect with civic services, share what matters in your neighbourhood, and help make Mumbai a better place to live.")}</p>
      </div>
      <div className="city-art" aria-hidden="true">
        <div className="map-wash" />
        <svg className="map-lines" viewBox="0 0 650 390" preserveAspectRatio="xMidYMid slice">
          <path d="M-30 300 C105 205 120 365 247 270 S390 180 470 220 580 125 700 165" />
          <path d="M50 420 C145 340 175 235 268 210 S340 98 430 105 514 40 610 4" />
          <path d="M-10 178 C85 190 114 110 222 125 S341 176 395 130 495 112 680 62" />
          <path d="M120 390 C180 310 222 340 290 280 S384 280 420 211 515 240 650 190" />
          <path d="M-10 75 C100 132 172 45 255 75 S385 65 462 20 565 12 640 40" />
        </svg>
        <span className="map-node node-one"><MapPin size={17} /></span>
        <span className="map-node node-two"><MapPin size={16} /></span>
        <span className="map-node node-three"><MapPin size={15} /></span>
        <div className="art-stat"><span className="stat-icon"><Users size={17} /></span><span><b>{t("Stronger together")}</b><small>{t("Every neighbourhood matters")}</small></span><ArrowRight size={16} /></div>
        <div className="art-building building-a"><i /><i /><i /><i /><i /><i /><span /></div>
        <div className="art-building building-b"><i /><i /><i /><i /><i /><i /><i /><i /><span /></div>
        <div className="art-building building-c"><i /><i /><i /><i /><i /><i /><span /></div>
        <div className="art-ground"><Leaf size={18} /><span /><span /><span /><Waves size={22} /></div>
      </div>
      <footer className="visual-footer"><span>{t("Built for a more responsive Mumbai")}</span><span className="footer-seal"><BadgeCheck size={15} /> {t("Civic access portal")}</span></footer>
    </section>
  );
}

function AuthLayout({ page, children, language, onLanguageChange }) {
  return (
    <main className="auth-layout">
      <CivicIllustration page={page} language={language} />
      <section className="form-panel">
        <LanguageSelector language={language} onChange={onLanguageChange} />
        <header className="mobile-brand"><Brand compact language={language} /></header>
        <div className="form-wrap">{children}</div>
        <footer className="form-footer"><span>© {translate(language, "BMC Civic Complaint Clustering System")}</span><span><ShieldCheck size={13} /> {translate(language, "Your information is protected")}</span></footer>
      </section>
    </main>
  );
}

function Field({ id, label, icon: Icon, error, hint, type = "text", ...props }) {
  return (
    <div className="field-group">
      <label htmlFor={id}>{label}</label>
      <div className={`input-shell ${error ? "has-error" : ""}`}>
        {Icon && <Icon size={17} aria-hidden="true" />}
        <input id={id} type={type} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined} {...props} />
      </div>
      {hint && !error && <small className="field-hint" id={`${id}-hint`}>{hint}</small>}
      {error && <small className="field-error" id={`${id}-error`}>{error}</small>}
    </div>
  );
}

function PasswordField({ id, label = "Password", error, hint, language = "en", ...props }) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="field-group">
      <label htmlFor={id}>{label}</label>
      <div className={`input-shell ${error ? "has-error" : ""}`}>
        <LockKeyhole size={17} aria-hidden="true" />
        <input id={id} type={visible ? "text" : "password"} autoComplete="new-password" aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined} {...props} />
        <button className="visibility-toggle" type="button" aria-label={translate(language, visible ? "Hide password" : "Show password")} onClick={() => setVisible((state) => !state)}>
          {visible ? <EyeOff size={17} /> : <Eye size={17} />}
        </button>
      </div>
      {hint && !error && <small className="field-hint" id={`${id}-hint`}>{hint}</small>}
      {error && <small className="field-error" id={`${id}-error`}>{error}</small>}
    </div>
  );
}

function PrimaryButton({ children, busy, icon: Icon = ArrowRight, disabled = false, ...props }) {
  return <button className="primary-button" {...props} disabled={busy || disabled}>{busy ? <LoaderCircle className="spin" size={18} /> : null}<span>{children}</span>{!busy && Icon && <Icon size={17} />}</button>;
}

function Notice({ type = "error", children }) {
  const Icon = type === "success" ? CircleCheck : CircleAlert;
  return <div className={`notice notice-${type}`} role={type === "error" ? "alert" : "status"}><Icon size={18} /><span>{children}</span></div>;
}

function PageHeading({ eyebrow, title, description, icon: Icon = ShieldCheck }) {
  return <div className="page-heading"><div className="heading-icon"><Icon size={21} /></div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2><p>{description}</p></div>;
}

function TextLink({ children, onClick, href = "#" }) {
  return <a href={href} onClick={(event) => { event.preventDefault(); onClick?.(); }}>{children}</a>;
}

function RegisterPage({ navigate, onPendingEmail, language }) {
  const t = (key, values) => translate(language, key, values);
  const [form, setForm] = useState({ name: "", email: "", password: "", confirm: "" });
  const [errors, setErrors] = useState({});
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const update = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }));

  const submit = async (event) => {
    event.preventDefault();
    const next = {};
    if (!form.name.trim()) next.name = t("Enter your name.");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) next.email = t("Enter a valid email address.");
    if (form.password.length < 8) next.password = t("Use at least 8 characters.");
    if (form.password !== form.confirm) next.confirm = t("The passwords don’t match.");
    setErrors(next);
    if (Object.keys(next).length) return;
    setBusy(true); setNotice(null);
    try {
      await apiRequest("/auth/register", { method: "POST", body: { name: form.name.trim(), email: form.email.trim(), password: form.password } });
      onPendingEmail(form.email.trim());
      navigate("/verify-email");
    } catch (error) { setNotice({ type: "error", text: t(error.message) }); }
    finally { setBusy(false); }
  };

  return <>
      <PageHeading eyebrow={t("CREATE YOUR ACCOUNT")} title={t("Join your community")} description={t("Create a secure account to access BMC citizen services.")} icon={Users} />
    {notice && <Notice>{notice.text}</Notice>}
    <form className="auth-form" onSubmit={submit} noValidate>
      <Field id="register-name" label={t("Full name")} icon={UserRound} autoComplete="name" placeholder={t("Your name")} value={form.name} onChange={update("name")} error={errors.name && t(errors.name)} />
      <Field id="register-email" label={t("Email address")} icon={Mail} type="email" autoComplete="email" placeholder="you@example.com" value={form.email} onChange={update("email")} error={errors.email && t(errors.email)} />
      <PasswordField id="register-password" language={language} label={t("Create password")} value={form.password} onChange={update("password")} error={errors.password && t(errors.password)} hint={t("At least 8 characters.")} />
      <PasswordField id="register-confirm" language={language} label={t("Confirm password")} value={form.confirm} onChange={update("confirm")} error={errors.confirm && t(errors.confirm)} />
      <PrimaryButton type="submit" busy={busy}>{t("Create account")}</PrimaryButton>
    </form>
    <p className="switch-prompt">{t("Already have an account?")} <TextLink onClick={() => navigate("/login")}>{t("Sign in")}</TextLink></p>
    <p className="privacy-note"><ShieldCheck size={15} /> {t("Your information is used only to provide citizen account access.")}</p>
  </>;
}

function LoginPage({ navigate, onLogin, language }) {
  const t = (key, values) => translate(language, key, values);
  const [form, setForm] = useState({ email: "", password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showResend, setShowResend] = useState(false);
  const [resendNotice, setResendNotice] = useState(null);
  const [resendBusy, setResendBusy] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);
  useEffect(() => {
    if (!resendCooldown) return;
    const timer = window.setInterval(() => setResendCooldown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [resendCooldown > 0]);
  const submit = async (event) => {
    event.preventDefault(); setError("");
    if (!form.email.trim() || !form.password) { setError(t("Enter your email address and password.")); return; }
    setBusy(true);
    try {
      const result = await apiRequest("/auth/login", { method: "POST", body: { email: form.email.trim(), password: form.password } });
      if (typeof result.access_token !== "string" || !result.user || typeof result.user !== "object") {
        throw new Error(t("The sign-in response was incomplete. Please try again."));
      }
      sessionStorage.setItem(TOKEN_KEY, result.access_token);
      onLogin(result.user);
    } catch (failure) { setError(t(failure.message)); }
    finally { setBusy(false); }
  };
  const resend = async (event) => {
    event.preventDefault(); setResendNotice(null); setResendBusy(true);
    try {
      const result = await apiRequest("/auth/resend-verification", { method: "POST", body: { email: form.email.trim() } });
      setResendNotice({ type: "success", text: t(result.message || "If the account exists and needs verification, a verification email has been sent.") });
      setResendCooldown(60);
    } catch (failure) { setResendNotice({ type: "error", text: t(failure.message) }); }
    finally { setResendBusy(false); }
  };
  return <>
    <PageHeading eyebrow={t("CITIZEN SIGN IN")} title={t("Welcome back")} description={t("Sign in to continue to your citizen services.")} icon={KeyRound} />
    {error && <Notice>{error}</Notice>}
    <form className="auth-form" onSubmit={submit} noValidate>
      <Field id="login-email" label={t("Email address")} icon={Mail} type="email" autoComplete="email" placeholder="you@example.com" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} />
      <div className="field-with-action"><PasswordField id="login-password" language={language} label={t("Password")} autoComplete="current-password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} /><TextLink onClick={() => navigate("/forgot-password")}>{t("Forgot password?")}</TextLink></div>
      <PrimaryButton type="submit" busy={busy}>{t("Sign in securely")}</PrimaryButton>
    </form>
    <button className="quiet-link" type="button" onClick={() => { setShowResend((value) => !value); setResendNotice(null); }}>{t("Need a verification email?")}</button>
    {showResend && <form className="resend-box" onSubmit={resend}>
      <p>{t("We’ll send a fresh verification link if your account needs one.")}</p>
      {resendNotice && <Notice type={resendNotice.type}>{resendNotice.text}</Notice>}
      <Field id="resend-email" label={t("Email address")} icon={Mail} type="email" autoComplete="email" placeholder="you@example.com" required value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} />
      <PrimaryButton type="submit" busy={resendBusy} disabled={resendCooldown > 0 || !form.email.trim()} icon={Mail}>{resendCooldown > 0 ? t("Try again in {seconds}s", { seconds: resendCooldown }) : t("Resend verification")}</PrimaryButton>
    </form>}
    <p className="switch-prompt">{t("New to BMC Civic?")} <TextLink onClick={() => navigate("/register")}>{t("Create account")}</TextLink></p>
  </>;
}

function VerifyPage({ navigate, initialEmail, language }) {
  const t = (key, values) => translate(language, key, values);
  const token = new URLSearchParams(window.location.search).get("token");
  const [status, setStatus] = useState(token ? "checking" : "pending");
  const [email, setEmail] = useState(initialEmail || "");
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const ran = useRef(false);
  useEffect(() => {
    if (!token || ran.current) return;
    ran.current = true;
    let active = true;
    apiRequest(`/auth/verify-email?token=${encodeURIComponent(token)}`)
      .then(() => { if (active) setStatus("verified"); })
      .catch((failure) => { if (active) { setStatus("invalid"); setNotice({ type: "error", text: t(failure.message) }); } });
    return () => { active = false; };
  }, [token]);
  useEffect(() => {
    if (!cooldown) return;
    const timer = window.setInterval(() => setCooldown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [cooldown > 0]);
  const resend = async (event) => {
    event.preventDefault(); setNotice(null); setBusy(true);
    try {
      const result = await apiRequest("/auth/resend-verification", { method: "POST", body: { email: email.trim() } });
      setNotice({ type: "success", text: t(result.message || "If the account exists and needs verification, a verification email has been sent.") });
      setCooldown(60);
    } catch (failure) { setNotice({ type: "error", text: t(failure.message) }); }
    finally { setBusy(false); }
  };
  if (status === "checking") return <LoadingScreen label={t("Verifying your email")} compact />;
  if (status === "verified") return <>
    <div className="state-illustration success-art"><span><Check size={30} /></span><i /><i /><i /></div>
    <PageHeading eyebrow={t("EMAIL CONFIRMED")} title={t("You’re verified")} description={t("Your email address is confirmed. Sign in to continue to your citizen account.")} icon={BadgeCheck} />
    <Notice type="success">{t("Your email address has been verified successfully.")}</Notice>
    <PrimaryButton type="button" onClick={() => navigate("/login")}>{t("Continue to sign in")}</PrimaryButton>
  </>;
  return <>
    <div className="state-illustration pending-art"><span><Mail size={28} /></span><i /><i /></div>
    <PageHeading eyebrow={t(status === "invalid" ? "LINK NEEDS ATTENTION" : "CHECK YOUR INBOX")} title={t(status === "invalid" ? "Let’s get you a fresh link" : "Verify your email")} description={t(status === "invalid" ? "This verification link can’t be used. Enter your email and we’ll help you continue." : "To activate your account, open the verification link sent to your email. You can request a fresh one below if needed.")} icon={Mail} />
    {notice && <Notice type={notice.type}>{notice.text}</Notice>}
    <form className="auth-form" onSubmit={resend} noValidate>
      <Field id="verify-email" label={t("Email address")} icon={Mail} type="email" autoComplete="email" placeholder="you@example.com" required value={email} onChange={(event) => setEmail(event.target.value)} />
      <PrimaryButton type="submit" busy={busy} disabled={cooldown > 0 || !email.trim()} icon={Mail}>{cooldown > 0 ? t("Try again in {seconds}s", { seconds: cooldown }) : t("Resend verification email")}</PrimaryButton>
    </form>
    <div className="help-note"><Clock3 size={16} /><span>{t("Verification links expire after 24 hours. If you’ve just requested one, allow a moment for delivery.")}</span></div>
    <p className="switch-prompt">{t("Already verified?")} <TextLink onClick={() => navigate("/login")}>{t("Sign in")}</TextLink></p>
  </>;
}

function ForgotPage({ navigate, language }) {
  const t = (key, values) => translate(language, key, values);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const submit = async (event) => {
    event.preventDefault(); setNotice(null);
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) { setNotice({ type: "error", text: t("Enter a valid email address.") }); return; }
    setBusy(true);
    try {
      const result = await apiRequest("/auth/forgot-password", { method: "POST", body: { email: email.trim() } });
      setNotice({ type: "success", text: t(result.message) });
    } catch (failure) { setNotice({ type: "error", text: t(failure.message) }); }
    finally { setBusy(false); }
  };
  return <>
    <PageHeading eyebrow={t("ACCOUNT RECOVERY")} title={t("Reset your password")} description={t("Enter the email address connected to your account. If it exists, we’ll send reset instructions.")} icon={KeyRound} />
    {notice && <Notice type={notice.type}>{notice.text}</Notice>}
    <form className="auth-form" onSubmit={submit} noValidate>
      <Field id="forgot-email" label={t("Email address")} icon={Mail} type="email" autoComplete="email" placeholder="you@example.com" value={email} onChange={(event) => setEmail(event.target.value)} />
      <PrimaryButton type="submit" busy={busy} icon={Mail}>{t("Send reset instructions")}</PrimaryButton>
    </form>
    <button className="back-link" type="button" onClick={() => navigate("/login")}><ArrowLeft size={16} /> {t("Back to sign in")}</button>
  </>;
}

function ResetPage({ navigate, language }) {
  const t = (key, values) => translate(language, key, values);
  const token = new URLSearchParams(window.location.search).get("token");
  const [form, setForm] = useState({ password: "", confirm: "" });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [complete, setComplete] = useState(false);
  const submit = async (event) => {
    event.preventDefault(); setNotice(null);
    if (!token) { setNotice({ type: "error", text: t("This reset link is missing its token. Request a new password reset email.") }); return; }
    if (form.password.length < 8) { setNotice({ type: "error", text: t("Use at least 8 characters for your new password.") }); return; }
    if (form.password !== form.confirm) { setNotice({ type: "error", text: t("The passwords don’t match.") }); return; }
    setBusy(true);
    try {
      await apiRequest("/auth/reset-password", { method: "POST", body: { token, new_password: form.password } });
      setComplete(true);
    } catch (failure) { setNotice({ type: "error", text: t(failure.message) }); }
    finally { setBusy(false); }
  };
  if (complete) return <>
    <div className="state-illustration success-art"><span><Check size={30} /></span><i /><i /><i /></div>
    <PageHeading eyebrow={t("PASSWORD UPDATED")} title={t("You’re all set")} description={t("Your new password is ready. Sign in with it to return to your citizen account.")} icon={BadgeCheck} />
    <Notice type="success">{t("Your password has been updated successfully.")}</Notice>
    <PrimaryButton type="button" onClick={() => navigate("/login")}>{t("Continue to sign in")}</PrimaryButton>
  </>;
  return <>
    <PageHeading eyebrow={t("SECURE YOUR ACCOUNT")} title={t("Choose a new password")} description={t("Create a new password for your BMC Civic account.")} icon={LockKeyhole} />
    {notice && <Notice>{notice.text}</Notice>}
    <form className="auth-form" onSubmit={submit} noValidate>
      <PasswordField id="reset-password" language={language} label={t("New password")} value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} hint={t("At least 8 characters.")} />
      <PasswordField id="reset-confirm" language={language} label={t("Confirm new password")} value={form.confirm} onChange={(event) => setForm({ ...form, confirm: event.target.value })} />
      <PrimaryButton type="submit" busy={busy}>{t("Save new password")}</PrimaryButton>
    </form>
    <p className="switch-prompt">{t("Link expired?")} <TextLink onClick={() => navigate("/forgot-password")}>{t("Request another")}</TextLink></p>
  </>;
}

function LoadingScreen({ label, compact = false }) {
  return <div className={`loading-screen ${compact ? "loading-compact" : ""}`} role="status"><LoaderCircle className="spin" size={compact ? 25 : 30} /><span>{label}</span></div>;
}

function Dashboard({ user, onLogout, navigate, language, onLanguageChange }) {
  const t = (key, values) => translate(language, key, values);
  const [menuOpen, setMenuOpen] = useState(false);
  const [summary, setSummary] = useState(null);
  const [reportsError, setReportsError] = useState(false);
  const [reportsLoading, setReportsLoading] = useState(false);
  const loadDashboard = async () => {
    setReportsLoading(true); setReportsError(false);
    try { setSummary(await apiRequest("/citizen/dashboard", { token: sessionStorage.getItem(TOKEN_KEY) })); }
    catch { setReportsError(true); }
    finally { setReportsLoading(false); }
  };
  useEffect(() => {
    let active = true;
    loadDashboard().catch(() => { if (active) setReportsError(true); });
    return () => { active = false; };
  }, []);
  const reports = summary?.recent_complaints;
  const latestReport = reports?.[0];
  return <main className="dashboard-shell">
    <header className="dashboard-header">
      <Brand compact language={language} onClick={() => navigate("/dashboard")} />
      <nav className="dashboard-nav" aria-label={t("Main navigation")}><a className="nav-active" href="/dashboard" onClick={(event) => { event.preventDefault(); navigate("/dashboard"); }}>{t("Overview")}</a><a href="/complaints" onClick={(event) => { event.preventDefault(); navigate("/complaints"); }}>{t("My reports")}</a><span className="nav-disabled" aria-label={t("Updates, coming in a future phase")}>{t("Updates")} <span>{t("Coming soon")}</span></span><a href="/track" onClick={(event) => { event.preventDefault(); navigate("/track"); }}>{t("Track Complaint")}</a><a href="/notifications" onClick={(event) => { event.preventDefault(); navigate("/notifications"); }}>{t("Notifications")}</a></nav>
      <div className="profile-area">
        <LanguageSelector language={language} onChange={onLanguageChange} compact />
        <NotificationBell navigate={navigate} language={language} />
        <div className="profile-menu-wrap">
          <button className="profile-button" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}><span className="avatar">{user.name?.trim()?.charAt(0)?.toUpperCase() || "C"}</span><span className="profile-name">{user.name}</span><ChevronDown size={15} /></button>
          {menuOpen && <div className="profile-dropdown"><div className="dropdown-user"><b>{user.name}</b><small>{user.email}</small></div><button onClick={onLogout}><LogOut size={16} /> {t("Sign out")}</button></div>}
        </div>
      </div>
    </header>
    <div className="dashboard-content" id="overview">
      <div className="dashboard-crumb"><span>{t("Citizen portal")}</span><span>/</span><b>{t("Overview")}</b></div>
      <section className="welcome-card">
        <div className="welcome-content"><span className="eyebrow"><span className="eyebrow-dot" /> {t("YOUR CIVIC SPACE")}</span><h1>{t("Good day, {name}.", { name: user.name?.split(" ")[0] || t("Citizen") })}</h1><p>{t("Your account is ready. Together, we can help make our neighbourhoods safer, cleaner and more connected.")}</p><button className="issue-button" onClick={() => navigate("/complaints/new")}><FileText size={17} /> {t("Report a civic issue")} <ArrowRight size={15} /></button></div>
        <div className="welcome-art" aria-hidden="true"><div className="welcome-orbit orbit-one" /><div className="welcome-orbit orbit-two" /><div className="welcome-pin"><MapPin size={31} /></div><span className="welcome-dot dot-a" /><span className="welcome-dot dot-b" /><span className="welcome-dot dot-c" /><Building2 className="welcome-building" size={70} strokeWidth={1.1} /></div>
      </section>
      <div className="section-title"><div><span className="eyebrow">{t("YOUR ACCOUNT AT A GLANCE")}</span><h2>{t("Service overview")}</h2></div><button className="quiet-link" onClick={loadDashboard} disabled={reportsLoading}><RefreshCw size={14} className={reportsLoading ? "spin" : ""} /> {t("Refresh")}</button></div>
      <section className="summary-grid" aria-label={t("Complaint summary")}>
        <SummaryCard icon={FileText} label={t("Complaints submitted")} value={summary?.total} status={t(reportsError ? "Could not load reports" : summary ? "From your account" : "Loading your reports…")} />
        <SummaryCard icon={Bell} label={t("Active reports")} value={summary?.active} status={t(reportsError ? "Could not load reports" : summary ? "Not yet resolved" : "Loading your reports…")} accent="orange" />
        <SummaryCard icon={CircleCheck} label={t("Resolved issues")} value={summary?.resolved} status={t(reportsError ? "Could not load reports" : summary ? "Updated from BMC status" : "Loading your reports…")} accent="green" />
      </section>
      <section className="activity-card" id="tracking"><div className="activity-heading"><div><span className="eyebrow">{t("STAY IN THE LOOP")}</span><h2>{t("Recent activity")}</h2></div><button className="quiet-link" onClick={() => navigate("/complaints")}>{t("View my reports")}</button></div>{reportsError ? <div className="empty-activity"><div><b>{t("Reports are temporarily unavailable")}</b><p>{t("Check your connection and refresh your dashboard.")}</p><button className="secondary-button" onClick={loadDashboard}>{t("Try again")}</button></div></div> : reportsLoading && !summary ? <div className="empty-activity"><div><b>{t("Loading recent activity…")}</b><p>{t("Your latest reports and status changes will appear here.")}</p></div></div> : latestReport ? <><button className="recent-report" onClick={() => navigate(`/complaints/${encodeURIComponent(latestReport.id)}`)}><span className="empty-icon"><FileText size={23} /></span><span><b>{latestReport.id} · {latestReport.text}</b><p>{formatDate(latestReport.created_at, language)} · {localizedStatus(language, latestReport.status || "unknown")}</p></span><ArrowRight size={17} /></button><div className="recent-status-activity">{(summary?.recent_activity || []).map((event, index) => <button key={`${event.complaint_id}-${index}`} onClick={() => navigate(`/complaints/${encodeURIComponent(event.complaint_id)}`)}><Clock3 size={14} /><span><b>{event.complaint_id}</b> · {localizedHistoryEvent(language, event.event_type)}{event.to_status ? ` — ${localizedStatus(language, event.to_status)}` : ""}</span><time>{formatDate(event.created_at, language)}</time></button>)}</div></> : <div className="empty-activity"><span className="empty-icon"><FileText size={23} /></span><div><b>{t("No complaints yet")}</b><p>{t("Your submitted complaints and their latest BMC status will appear here.")}</p></div></div>}</section>
      <section className="activity-card"><div className="activity-heading"><div><span className="eyebrow">{t("FOLLOWED COMPLAINTS")}</span><h2>{t("Followed complaints")}</h2></div><button className="quiet-link" onClick={() => navigate("/track")}>{t("Track another complaint")}</button></div>{summary?.followed_complaints?.length ? summary.followed_complaints.map(item => <article className="recent-report" key={item.id}><div><b>{item.id} · {item.text}</b><p>{item.location || item.ward || ""} · {localizedStatus(language, item.status)}</p></div><button onClick={() => navigate(`/track?reference=${encodeURIComponent(item.id)}`)}>{t("View tracking")}</button><button onClick={async () => { await apiRequest(`/citizen/follows/${encodeURIComponent(item.id)}`, { method: "PUT", body: { active: false }, token: sessionStorage.getItem(TOKEN_KEY) }); loadDashboard(); }}>{t("Unfollow")}</button></article>) : <p>{t("Complaints you follow will appear here.")}</p>}</section>
      <footer className="dashboard-footer"><span><ShieldCheck size={15} /> {t("Signed in as a verified citizen")}</span><span>{t("BMC Civic Complaint Clustering System")}</span></footer>
    </div>
  </main>;
}

function SummaryCard({ icon: Icon, label, value, status, accent = "blue" }) {
  return <article className={`summary-card summary-${accent}`}><div className="summary-top"><span className="summary-icon"><Icon size={19} /></span><span className="summary-metric">{value ?? "—"}</span></div><h3>{label}</h3><p>{status}</p></article>;
}

export default App;
