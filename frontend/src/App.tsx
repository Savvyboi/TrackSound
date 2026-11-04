import { Compass } from "lucide-react";
import { lazy, Suspense } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { JobsWatcher } from "./api/jobs";
import { Button } from "./components/ui/Button";
import { EmptyState, Spinner } from "./components/ui/misc";
import { ConfirmHost, GlobalTooltip } from "./components/ui/overlay";
import { Toaster } from "./components/ui/toast";
import { HomePage } from "./features/home/HomePage";

// the workspace pulls in MapLibre (~1 MB): load it only when a case is opened
const WorkspacePage = lazy(() => import("./features/workspace/WorkspacePage").then((m) => ({ default: m.WorkspacePage })));

export function App() {
  return (
    <>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route
          path="/cases/:id"
          element={
            <Suspense
              fallback={
                <div className="flex h-full items-center justify-center">
                  <Spinner className="size-6 text-fg-faint" />
                </div>
              }
            >
              <WorkspacePage />
            </Suspense>
          }
        />
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
