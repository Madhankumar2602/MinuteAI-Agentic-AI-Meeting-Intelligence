import { useQuery } from "@tanstack/react-query";
import {
  CalendarDays,
  CheckSquare,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquareText,
  Plus,
  Sparkles,
  Workflow,
} from "lucide-react";
import { useState } from "react";
import { Link, NavLink, Navigate, Outlet, useLocation } from "react-router";

import { api } from "../api/endpoints";
import { useAuth } from "../auth/useAuth";
import { ThemeToggle } from "./ThemeToggle";
import { Avatar, PageLoading } from "./ui";

function BrandMark() {
  return (
    <div className="sidebar-brand">
      <span className="brand-logo" aria-hidden>
        <Sparkles size={17} />
      </span>
      <div>
        <div className="brand-name">MinuteAI</div>
        <div className="brand-tag">Meeting intelligence</div>
      </div>
    </div>
  );
}

export function Layout() {
  const { user, loading, logout } = useAuth();
  const location = useLocation();
  // The mobile menu is open for the page it was opened on; navigating closes
  // it, derived from the path rather than reset in an effect.
  const [menuOpenOn, setMenuOpenOn] = useState<string | null>(null);
  const menuOpen = menuOpenOn === location.pathname;
  const setMenuOpen = (open: boolean) => setMenuOpenOn(open ? location.pathname : null);

  // The overdue count in the sidebar shares the dashboard query's cache.
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, enabled: Boolean(user) });
  const overdue = dashboard.data?.action_items.overdue ?? 0;

  if (loading) {
    return (
      <div className="content">
        <PageLoading />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;

  return (
    <div className="app-shell">
      <aside className={`sidebar${menuOpen ? " open" : ""}`} aria-label="Sidebar">
        <BrandMark />
        <Link to="/meetings/new" className="btn btn-gradient" style={{ margin: "0 2px 12px" }}>
          <Plus size={16} /> New meeting
        </Link>

        <nav aria-label="Main" style={{ display: "contents" }}>
          <NavLink to="/" end className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <LayoutDashboard size={18} /> Dashboard
          </NavLink>
          <NavLink to="/meetings" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <CalendarDays size={18} /> Meetings
          </NavLink>
          <NavLink to="/action-items" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <CheckSquare size={18} /> Action items
            {overdue > 0 && <span className="nav-count" aria-label={`${overdue} overdue`}>{overdue}</span>}
          </NavLink>

          <div className="nav-section-label">Coming next</div>
          {/* Visible but not clickable: these arrive in later milestones. They
              are labelled rather than shipped as empty pages, so nothing in the
              UI pretends to work before it does. */}
          <span className="nav-item disabled" aria-disabled="true" title="Available in milestone M7">
            <MessageSquareText size={18} /> Ask your meetings <span className="nav-soon">M7</span>
          </span>
          <span className="nav-item disabled" aria-disabled="true" title="Available in milestone M8">
            <Workflow size={18} /> Agent follow-ups <span className="nav-soon">M8</span>
          </span>
        </nav>

        <div className="sidebar-footer">
          <div className="user-chip">
            <Avatar name={user.full_name} />
            <div className="who">
              <div className="strong truncate">{user.full_name}</div>
              <div className="faint xs truncate">{user.email}</div>
            </div>
            <span className="spacer" />
            <ThemeToggle />
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={logout} style={{ justifyContent: "flex-start" }}>
            <LogOut size={15} /> Sign out
          </button>
        </div>
      </aside>

      {menuOpen && <div className="sidebar-scrim" onClick={() => setMenuOpen(false)} aria-hidden />}

      <div className="main-area">
        <header className="topbar">
          <button type="button" className="btn btn-ghost btn-icon" aria-label="Open menu" onClick={() => setMenuOpen(true)}>
            <Menu size={19} />
          </button>
          <span className="brand-name">MinuteAI</span>
          <span className="spacer" />
          <ThemeToggle />
        </header>
        <main className="content" key={location.pathname}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
