import { ArrowRight, BrainCircuit, CheckCircle2, Mic, Search, Sparkles } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";

import { useAuth } from "../auth/useAuth";
import { ThemeToggle } from "../components/ThemeToggle";
import { ErrorBanner, Spinner } from "../components/ui";

const FEATURES = [
  { icon: Mic, title: "Paste or upload", text: "Transcripts or recordings, transcribed automatically." },
  { icon: BrainCircuit, title: "Decisions and owners", text: "Every action item with who, what, and when." },
  { icon: CheckCircle2, title: "Grounded in evidence", text: "Each item links to the exact words that support it." },
  { icon: Search, title: "Remember everything", text: "Ask across all your meetings — coming soon." },
];

function AuthLayout({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <div className="auth-split">
      <aside className="auth-brand">
        <div className="row">
          <span className="brand-logo" aria-hidden><Sparkles size={17} /></span>
          <span className="brand-name">MinuteAI</span>
        </div>
        <div>
          <h2>Meetings end. The decisions shouldn&apos;t disappear.</h2>
          <p className="lede">MinuteAI turns every conversation into a summary, clear decisions, and action items your team can act on.</p>
          <ul className="feature-list">
            {FEATURES.map(({ icon: Icon, title: t, text }) => (
              <li key={t}>
                <span className="fi"><Icon size={17} aria-hidden /></span>
                <div>
                  <strong>{t}</strong>
                  <span>{text}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="xs" style={{ opacity: 0.7 }}>Final-year project · AI meeting intelligence</p>
      </aside>
      <main className="auth-panel" style={{ position: "relative" }}>
        <div className="theme-float"><ThemeToggle /></div>
        <div className="auth-form">
          <h1>{title}</h1>
          <p className="subtitle">{subtitle}</p>
          {children}
        </div>
      </main>
    </div>
  );
}

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  if (user) return <Navigate to={from} replace />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout title="Welcome back" subtitle="Sign in to your meeting workspace.">
      <form className="stack" onSubmit={onSubmit} noValidate>
        <ErrorBanner error={error} />
        <div className="field">
          <label className="label" htmlFor="email">Email</label>
          <input className="input" id="email" type="email" autoComplete="email" placeholder="you@company.com" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label className="label" htmlFor="password">Password</label>
          <input className="input" id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <button type="submit" className="btn btn-gradient" disabled={submitting || !email || !password}>
          {submitting ? <Spinner label="Signing in" /> : <>Sign in <ArrowRight size={16} /></>}
        </button>
      </form>
      <p className="auth-switch">
        New to MinuteAI? <Link to="/register">Create an account</Link>
      </p>
    </AuthLayout>
  );
}

export const MIN_PASSWORD_LENGTH = 8;

export function RegisterPage() {
  const { user, register } = useAuth();
  const navigate = useNavigate();

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  if (user) return <Navigate to="/" replace />;

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(new Error(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`));
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await register(email.trim(), password, fullName.trim());
      navigate("/", { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout title="Create your account" subtitle="Start turning meetings into decisions and action items.">
      <form className="stack" onSubmit={onSubmit} noValidate>
        <ErrorBanner error={error} />
        <div className="field">
          <label className="label" htmlFor="fullName">Full name</label>
          <input className="input" id="fullName" type="text" autoComplete="name" required value={fullName} onChange={(e) => setFullName(e.target.value)} />
        </div>
        <div className="field">
          <label className="label" htmlFor="email">Email</label>
          <input className="input" id="email" type="email" autoComplete="email" placeholder="you@company.com" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label className="label" htmlFor="password">Password</label>
          <input
            className="input"
            id="password"
            type="password"
            autoComplete="new-password"
            required
            aria-invalid={tooShort}
            aria-describedby="password-hint"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <span id="password-hint" className="hint">At least {MIN_PASSWORD_LENGTH} characters.</span>
        </div>
        <button type="submit" className="btn btn-gradient" disabled={submitting || !fullName || !email || !password}>
          {submitting ? <Spinner label="Creating account" /> : <>Create account <ArrowRight size={16} /></>}
        </button>
      </form>
      <p className="auth-switch">
        Already have an account? <Link to="/login">Sign in</Link>
      </p>
    </AuthLayout>
  );
}
