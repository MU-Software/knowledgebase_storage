import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { useMe } from '../hooks'
import QueryBoundary from './QueryBoundary'

const Gate = () => {
  const { data } = useMe()
  const { pathname, search } = useLocation()

  return data ? <Outlet /> : <Navigate to="/login" replace state={{ from: `${pathname}${search}` }} />
}

const RequireAuth = () => (
  <QueryBoundary>
    <Gate />
  </QueryBoundary>
)

export default RequireAuth
