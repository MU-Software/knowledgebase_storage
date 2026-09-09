import { Route, Routes } from 'react-router-dom'

import Layout from './Layout'
import Jobs from './pages/Jobs'
import NoteDetail from './pages/NoteDetail'
import Notes from './pages/Notes'
import Projects from './pages/Projects'
import Search from './pages/Search'
import Settings from './pages/Settings'

const App = () => (
  <Routes>
    <Route element={<Layout />}>
      <Route path="/" element={<Projects />} />
      <Route path="/projects/:project" element={<Notes />} />
      <Route path="/notes/*" element={<NoteDetail />} />
      <Route path="/search" element={<Search />} />
      <Route path="/jobs" element={<Jobs />} />
      <Route path="/settings" element={<Settings />} />
    </Route>
  </Routes>
)

export default App
