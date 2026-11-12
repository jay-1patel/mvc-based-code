import type { LayerDetail, MenuButton } from './types'

/**
 * Where a piece of tenant data came from — the remake's source chips.
 *
 * Only three provenances can be told apart with the data the APIs already
 * return, and only for layered profile content (menu buttons and anything
 * else merged from the same layers):
 *
 *   - `pdf`     — the item's id appears in the tenant's FILE layer
 *                 (`clients/<id>` pack on the server). That pack is what
 *                 onboarding produces from the tenant's kit/PDF, so an
 *                 item that only exists there arrived with the tenant's
 *                 documents, not from the console and not from defaults.
 *   - `manual`  — the item's id appears in the DB layer. The DB layer is
 *                 written through the admin console (drafts, publishes,
 *                 change requests), so the item was typed/changed by an
 *                 admin. A DB entry wins over a file entry: an admin who
 *                 edits a PDF-born item has made it theirs.
 *   - `default` — the item exists only in the resolved profile because a
 *                 layer below the tenant's own supplied it: the vertical /
 *                 system defaults.
 *
 * What this CANNOT do (no API field carries it, so we do not pretend):
 *   - tell a PDF-born file-layer item from a hand-seeded config pack item;
 *   - give a source for content that does not travel through the layers
 *     (uploaded files, crawl items — the crawl UI already labels those
 *     as website content by construction).
 */
export type DataSource = 'pdf' | 'manual' | 'default'

export const SOURCE_LABELS: Record<DataSource, string> = {
  pdf: 'From PDF',
  manual: 'Typed by admin',
  default: 'System default',
}

function layerButtonIds(layer: Record<string, unknown> | undefined): Set<string> {
  const menu = (layer as { menu?: { buttons?: { id?: unknown }[] } } | undefined)?.menu
  const ids = new Set<string>()
  for (const b of menu?.buttons ?? []) {
    if (b && typeof b.id === 'string') ids.add(b.id)
  }
  return ids
}

/** The source of one menu button, from the tenant's layer detail. */
export function menuButtonSource(
  button: Pick<MenuButton, 'id'>,
  detail: Pick<LayerDetail, 'layers'> | null | undefined,
): DataSource {
  if (!detail) return 'default'
  if (layerButtonIds(detail.layers.db).has(button.id)) return 'manual'
  if (layerButtonIds(detail.layers.file).has(button.id)) return 'pdf'
  return 'default'
}
