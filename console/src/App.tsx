import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { apiGet } from './api/client'
import Audit from './pages/Audit'
import Intake from './pages/Intake'
import PacketReview from './pages/PacketReview'
import ReviewQueue from './pages/ReviewQueue'

function ApiStatus() {
  const [status, setStatus] = useState('checking…')
  useEffect(() => {
    apiGet('/health')
      .then((h) => setStatus(`API ${h.status} · v${h.version}`))
      .catch(() => setStatus('API unreachable'))
  }, [])
  return <span className="api-status">{status}</span>
}

export default function App() {
  return (
    <BrowserRouter>
      <header className="topbar">
        <strong>Suraksha Setu</strong>
        <nav>
          <NavLink to="/" end>Intake</NavLink>
          <NavLink to="/review">Review queue</NavLink>
        </nav>
        <ApiStatus />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Intake />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/review/:claimId" element={<PacketReview />} />
          <Route path="/audit/:caseId" element={<Audit />} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
