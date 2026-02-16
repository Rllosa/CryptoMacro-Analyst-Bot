import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom'
import './App.css'

// View stubs (to be implemented in DEL-7 through DEL-12)
import CommandCenter from './views/CommandCenter'
import AssetDetail from './views/AssetDetail'
import MacroDashboard from './views/MacroDashboard'
import OnChainIntelligence from './views/OnChainIntelligence'
import IntelligenceCenter from './views/IntelligenceCenter'
import Evaluation from './views/Evaluation'

function App() {
  return (
    <Router>
      <div className="app">
        <nav className="sidebar">
          <h1>CryptoMacro</h1>
          <ul>
            <li><Link to="/">Command Center</Link></li>
            <li><Link to="/asset">Asset Detail</Link></li>
            <li><Link to="/macro">Macro Dashboard</Link></li>
            <li><Link to="/onchain">On-Chain Intel</Link></li>
            <li><Link to="/intel">Intelligence Center</Link></li>
            <li><Link to="/eval">Evaluation</Link></li>
          </ul>
        </nav>
        <main className="content">
          <Routes>
            <Route path="/" element={<CommandCenter />} />
            <Route path="/asset" element={<AssetDetail />} />
            <Route path="/macro" element={<MacroDashboard />} />
            <Route path="/onchain" element={<OnChainIntelligence />} />
            <Route path="/intel" element={<IntelligenceCenter />} />
            <Route path="/eval" element={<Evaluation />} />
          </Routes>
        </main>
      </div>
    </Router>
  )
}

export default App
