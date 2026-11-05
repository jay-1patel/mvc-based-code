import { useAsync } from '@/lib/hooks'
import { tenantsApi } from '@/lib/tenants'

export function useResolved(tenantId: string | null) {
  return useAsync(
    (signal) => tenantsApi.resolved(tenantId as string, signal),
    [tenantId],
  )
}

export function useTenantDetail(tenantId: string | null) {
  return useAsync(
    (signal) => tenantsApi.detail(tenantId as string, signal),
    [tenantId],
  )
}

export function useVersions(tenantId: string | null) {
  return useAsync(() => tenantsApi.versions(tenantId as string), [tenantId])
}
