import { Compass } from "lucide-react";
import { Link } from "react-router";

import { EmptyState } from "../components/ui";

export function NotFoundPage() {
  return (
    <div className="card">
      <EmptyState icon={Compass} title="Page not found" action={<Link className="btn" to="/">Back to dashboard</Link>}>
        The page you're looking for doesn't exist, or you don't have access to it.
      </EmptyState>
    </div>
  );
}
