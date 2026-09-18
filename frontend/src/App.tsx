import { useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router";

import { AppProviders } from "./AppProviders";
import { Layout } from "./components/Layout";
import { makeQueryClient } from "./lib/queryClient";
import { ActionItemsPage } from "./pages/ActionItemsPage";
import { AskPage } from "./pages/AskPage";
import { LoginPage, RegisterPage } from "./pages/AuthPages";
import { DashboardPage } from "./pages/DashboardPage";
import { FollowUpsPage } from "./pages/FollowUpsPage";
import { MeetingDetailPage } from "./pages/MeetingDetailPage";
import { MeetingsPage } from "./pages/MeetingsPage";
import { NewMeetingPage } from "./pages/NewMeetingPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { SearchPage } from "./pages/SearchPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="meetings" element={<MeetingsPage />} />
        <Route path="meetings/new" element={<NewMeetingPage />} />
        <Route path="meetings/:meetingId" element={<MeetingDetailPage />} />
        <Route path="action-items" element={<ActionItemsPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="ask" element={<AskPage />} />
        <Route path="follow-ups" element={<FollowUpsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export function App() {
  const [queryClient] = useState(makeQueryClient);
  return (
    <AppProviders queryClient={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AppProviders>
  );
}
