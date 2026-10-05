import { Navigate, Route, Routes } from "react-router-dom";

import { RequireAuth } from "./app/RequireAuth";
import { Shell } from "./app/Shell";
import { AgentDetailPage } from "./features/agents/AgentDetailPage";
import { AgentListPage } from "./features/agents/AgentListPage";
import { ChangePasswordPage } from "./features/auth/ChangePasswordPage";
import { LoginPage } from "./features/auth/LoginPage";
import { EventsPage } from "./features/events/EventsPage";
import { OverviewPage } from "./features/overview/OverviewPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Shell />
          </RequireAuth>
        }
      >
        <Route index element={<OverviewPage />} />
        <Route path="agents" element={<AgentListPage />} />
        <Route path="agents/:id" element={<AgentDetailPage />} />
        <Route path="events" element={<EventsPage />} />
        <Route path="password"element={<ChangePasswordPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
