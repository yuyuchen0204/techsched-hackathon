import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes, Link, Navigate, useLocation } from 'react-router-dom'
import './index.css'
import Dashboard from './pages/Dashboard'
import Customer from './pages/Customer'
import CustomerOrder from './pages/CustomerOrder'
import Technician from './pages/Technician'

function Nav() {
  const loc = useLocation()
  const tab = (to: string, label: string) => (
    <Link to={to} className={`px-3 py-1.5 text-sm rounded-md ${(to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(to)) ? 'bg-gray-900 text-white' : 'text-gray-600 hover:bg-gray-200'}`}>{label}</Link>
  )
  return (
    <div className="flex items-center gap-2 border-b border-gray-200 bg-white px-3 py-1.5">
      <span className="mr-2 text-sm font-bold tracking-tight">TechSched</span>
      {tab('/', 'Dispatcher')}
      {tab('/customer', 'Customer app')}
      {tab('/technician', 'Technician app')}
      <span className="ml-auto hidden text-[11px] text-gray-400 sm:inline">demo · synthetic data · notifications simulated</span>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <div className="flex h-full flex-col">
        <Nav />
        <div className="min-h-0 flex-1">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/customer" element={<Customer />} />
            <Route path="/customer/orders/:id" element={<CustomerOrder />} />
            <Route path="/technician" element={<Technician />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  </StrictMode>,
)
