/**
 * WhatsApp text formatting → renderable segments.
 *
 * WhatsApp markup is deliberately tiny: *bold*, _italic_, ~strikethrough~,
 * ```monospace``` and `monospace`. The bot's answers arrive already
 * normalized into this markup by the backend delivery layer, so the
 * replica console only has to *render* it the way the WhatsApp client
 * does — this parser is that rendering rule, kept pure for unit tests.
 *
 * A marker pair only counts when it wraps non-empty text without spaces
 * hugging the markers (WhatsApp's own rule of thumb); anything else is
 * literal text, so prices like "Rs 5 * 2" never turn into stray bold.
 */

export interface FormatSegment {
  text: string
  bold?: boolean
  italic?: boolean
  strike?: boolean
  mono?: boolean
}

const PATTERN =
  /(```[^`]+```|`[^`\n]+`|\*[^*\n]+?\*|_[^_\n]+?_|~[^~\n]+?~)/g

function isWrapped(segment: string, marker: string): boolean {
  if (!segment.startsWith(marker) || !segment.endsWith(marker)) return false
  const inner = segment.slice(marker.length, segment.length - marker.length)
  return inner.length > 0 && inner.trim() === inner
}

export function parseWhatsAppFormat(text: string): FormatSegment[] {
  if (!text) return []
  const segments: FormatSegment[] = []
  for (const part of text.split(PATTERN)) {
    if (!part) continue
    if (isWrapped(part, '```')) {
      segments.push({ text: part.slice(3, -3), mono: true })
    } else if (isWrapped(part, '`')) {
      segments.push({ text: part.slice(1, -1), mono: true })
    } else if (isWrapped(part, '*')) {
      segments.push({ text: part.slice(1, -1), bold: true })
    } else if (isWrapped(part, '_')) {
      segments.push({ text: part.slice(1, -1), italic: true })
    } else if (isWrapped(part, '~')) {
      segments.push({ text: part.slice(1, -1), strike: true })
    } else {
      segments.push({ text: part })
    }
  }
  return segments
}

const URL_PATTERN = /(https?:\/\/[^\s<]+)/g

/** Split a plain segment further so URLs can render as links. */
export function splitLinks(
  segment: FormatSegment,
): { text: string; href?: string; segment: FormatSegment }[] {
  if (segment.mono) return [{ text: segment.text, segment }]
  const out: { text: string; href?: string; segment: FormatSegment }[] = []
  for (const piece of segment.text.split(URL_PATTERN)) {
    if (!piece) continue
    if (/^https?:\/\//.test(piece)) {
      // Trailing punctuation is not part of the link WhatsApp tappable area.
      const match = piece.match(/^(https?:\/\/[^\s<]+?)([.,;:!?)]+)?$/)
      const url = match?.[1] ?? piece
      out.push({ text: url, href: url, segment })
      if (match?.[2]) out.push({ text: match[2], segment })
    } else {
      out.push({ text: piece, segment })
    }
  }
  return out
}
