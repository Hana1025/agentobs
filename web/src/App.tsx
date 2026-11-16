import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Overview from "./pages/Overview";
import TraceList from "./pages/TraceList";
import TraceDetail from "./pages/TraceDetail";
import ReviewQueue from "./pages/ReviewQueue";
import EvalPage from "./pages/Eval";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="traces" element={<TraceList />} />
        <Route path="traces/:traceId" element={<TraceDetail />} />
        <Route path="review" element={<ReviewQueue />} />
        <Route path="eval" element={<EvalPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
