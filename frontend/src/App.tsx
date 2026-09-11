import { Route, Routes } from 'react-router-dom'

import Layout from './Layout'
import RequireAuth from './components/RequireAuth'
import APIKeys from './pages/APIKeys'
import Jobs from './pages/Jobs'
import Login from './pages/Login'
import NoteDetail from './pages/NoteDetail'
import Notes from './pages/Notes'
import Projects from './pages/Projects'
import Search from './pages/Search'
import Settings from './pages/Settings'

const App = () => (
  <Routes>
    <Route path="/login" element={<Login />} />
    <Route element={<RequireAuth />}>
      <Route element={<Layout />}>
        <Route path="/" element={<Projects />} />
        <Route path="/projects/:project" element={<Notes />} />
        <Route path="/notes/*" element={<NoteDetail />} />
        <Route path="/search" element={<Search />} />
        <Route path="/jobs" element={<Jobs />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/api-keys" element={<APIKeys />} />
      </Route>
    </Route>
  </Routes>
)

export default App
