import {
  Building2,
  Calculator,
  Code2,
  Landmark,
  Plane,
  ShoppingBag,
  Stethoscope,
  type LucideIcon,
} from 'lucide-react'

const ICONS: Record<string, LucideIcon> = {
  Building2,
  Calculator,
  Code2,
  Landmark,
  Plane,
  ShoppingBag,
  Stethoscope,
}

export function verticalIcon(name: string): LucideIcon {
  return ICONS[name] ?? Building2
}

export function VerticalGlyph({ name, className }: { name: string; className?: string }) {
  const Icon = verticalIcon(name)
  return <Icon className={className} />
}
