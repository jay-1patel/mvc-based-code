import { useEffect, useState } from 'react'

/**
 * Hash routing, hand-rolled. Routes look like:
 *   #/tenants
 *   #/tenants/acme/profile
 *   #/tenants/acme/versions
 */

function currentPath(): string {
  const raw = window.location.hash.replace(/^#/, '')
  return raw || '/tenants'
}

export function navigate(path: string) {
  const next = path.startsWith('#') ? path : `#${path}`
  if (window.location.hash === next) return
  window.location.hash = next
}
export function useRoute(): string {
  const [path, setPath] = useState(currentPath())
  useEffect(() => {
    const onChange = () => setPath(currentPath())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return path
}

export interface RouteMatch {
  path: string
  segments: string[]
  /** Tenant id when the route is scoped to one. */
  tenantId: string | null
  /** The view name after the tenant segment. */
  view: string
  /** Query part of the hash, e.g. #/tenants/x/menu-edit?focus=menu_quote */
  query: URLSearchParams
}

export function parseRoute(path: string): RouteMatch {
  const [pathname, search = ''] = path.split('?')
  const segments = pathname.split('/').filter(Boolean)
  const query = new URLSearchParams(search)
  if (segments[0] !== 'tenants' || !segments[1]) {
    return { path: pathname, segments, tenantId: null, view: segments[0] ?? 'tenants', query }
  }
  return {
    path: pathname,
    segments,
    tenantId: decodeURIComponent(segments[1]),
    view: segments[2] ?? 'overview',
    query,
  }
}
