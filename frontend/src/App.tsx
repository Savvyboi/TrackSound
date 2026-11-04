import { Compass } from "lucide-react";
import { Link, Route, Routes } from "react-router-dom";
import { JobsWatcher } from "./api/jobs";
import { Button } from "./components/ui/Button";
import { EmptyState } from "./components/ui/misc";
import { ConfirmHost, GlobalTooltip } from "./components/ui/overlay";
import { Toaster } from "./components/ui/toast";
import { HomePage } from "./features/home/HomePage";
import { WorkspacePage } from "./features/workspace/WorkspacePage";

export function App() {
  return (
    <>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/cases/:id" element={<WorkspacePage />} />
        <Route
          path="*"
          element={
            <div className="flex h-full items-center justify-center">
              <EmptyState
                icon={<Compass />}
                title="Page not found"
                action={
                  <Link to="/">
                    <Button variant="primary">Back to cases</Button>
                  </Link>
                }
              >
                There's nothing at this address.
              </EmptyState>
            </div>
          }
        />
      </Routes>
      <JobsWatcher />
      <Toaster />
      <GlobalTooltip />
      <ConfirmHost />
    </>
  );
}
